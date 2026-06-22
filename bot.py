import discord
from discord import ui
from discord.ext import commands
import gspread
from google.oauth2.service_account import Credentials
import os
import json
import sys
from datetime import datetime, timedelta

# ===================== ПРОВЕРКА ОБЯЗАТЕЛЬНЫХ ПЕРЕМЕННЫХ =====================
required_vars = {
    'DISCORD_TOKEN': 'Токен бота',
    'GOOGLE_CREDENTIALS': 'JSON-ключ сервисного аккаунта Google',
    'SHEET_ID': 'ID Google таблицы',
    'DISCORD_CHANNEL_ID': 'ID канала для меню',
    'ALLOWED_USERS': 'Список ID пользователей через запятую'
}

missing = []
for var, desc in required_vars.items():
    if not os.getenv(var):
        missing.append(f"{var} ({desc})")

if missing:
    print("❌ Ошибка: отсутствуют обязательные переменные окружения:")
    for m in missing:
        print(f"   - {m}")
    sys.exit(1)

TOKEN = os.getenv('DISCORD_TOKEN')
SHEET_ID = os.getenv('SHEET_ID')
CHANNEL_ID = os.getenv('DISCORD_CHANNEL_ID')
ALLOWED_USERS_STR = os.getenv('ALLOWED_USERS')

try:
    ALLOWED_USERS = [int(x.strip()) for x in ALLOWED_USERS_STR.split(',') if x.strip()]
except ValueError:
    print("❌ Ошибка: ALLOWED_USERS содержит нечисловые значения.")
    sys.exit(1)

if not ALLOWED_USERS:
    print("❌ Ошибка: ALLOWED_USERS не должна быть пустой.")
    sys.exit(1)

try:
    CHANNEL_ID_INT = int(CHANNEL_ID)
except ValueError:
    print("❌ Ошибка: DISCORD_CHANNEL_ID должно быть числом.")
    sys.exit(1)

print("=== КОНФИГУРАЦИЯ ===")
print(f"SHEET_ID: {SHEET_ID}")
print(f"CHANNEL_ID: {CHANNEL_ID_INT}")
print(f"ALLOWED_USERS: {ALLOWED_USERS}")
print("=====================")

# ===================== ФУНКЦИИ ПРОВЕРКИ =====================
def is_allowed(user_id: int) -> bool:
    return user_id in ALLOWED_USERS

def is_guild_only(interaction_or_ctx) -> bool:
    if isinstance(interaction_or_ctx, discord.Interaction):
        return interaction_or_ctx.guild is not None
    elif isinstance(interaction_or_ctx, commands.Context):
        return interaction_or_ctx.guild is not None
    return False

# ===================== ПОДКЛЮЧЕНИЕ К GOOGLE SHEETS =====================
print("=== ДИАГНОСТИКА GOOGLE SHEETS ===")
print(f"Текущее время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S %Z')}")

creds_json = os.getenv('GOOGLE_CREDENTIALS')
try:
    creds_dict = json.loads(creds_json)
    print("✅ JSON распарсен")
    print(f"   client_email: {creds_dict.get('client_email')}")
    print(f"   project_id: {creds_dict.get('project_id')}")
except json.JSONDecodeError as e:
    print(f"❌ Ошибка парсинга JSON: {e}")
    sys.exit(1)

try:
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SHEET_ID).sheet1
    print("✅ Таблица открыта")

    all_values = sheet.get_all_values()
    if not all_values:
        print("❌ Таблица пуста, отсутствуют заголовки.")
        sys.exit(1)

    header_row = all_values[0]
    column_index = {}
    for i, col_name in enumerate(header_row):
        col_name = col_name.strip()
        if not col_name:
            col_name = f"Column{i+1}"
        if col_name in column_index:
            suffix = 1
            while f"{col_name}_{suffix}" in column_index:
                suffix += 1
            col_name = f"{col_name}_{suffix}"
        column_index[col_name] = i

    records = []
    for row in all_values[1:]:
        if not any(row):
            continue
        record = {}
        for col_name, idx in column_index.items():
            record[col_name] = row[idx] if idx < len(row) else ''
        records.append(record)

    print(f"✅ Данные прочитаны (записей: {len(records)}, столбцов: {len(column_index)})")

    # ===== ФОРМАТИРОВАНИЕ ARIAL 12 =====
    def apply_formatting_to_range(sheet_obj, start_row, end_row, start_col, end_col):
        body = {
            "requests": [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_obj.id,
                            "startRowIndex": start_row,
                            "endRowIndex": end_row,
                            "startColumnIndex": start_col,
                            "endColumnIndex": end_col
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "textFormat": {
                                    "fontFamily": "Arial",
                                    "fontSize": 12
                                }
                            }
                        },
                        "fields": "userEnteredFormat.textFormat(fontFamily,fontSize)"
                    }
                }
            ]
        }
        sheet_obj.spreadsheet.batch_update(body)

    if len(all_values) > 0 and len(all_values[0]) > 0:
        rows = len(all_values)
        cols = max(len(row) for row in all_values) if all_values else 0
        if rows > 0 and cols > 0:
            apply_formatting_to_range(sheet, 0, rows, 0, cols)
            print(f"✅ Применено форматирование Arial 12 ко всем {rows} строкам")

    # ===== ФУНКЦИЯ ДЛЯ ЗАПОЛНЕНИЯ ПРОПУЩЕННЫХ СРОКОВ ПОГАШЕНИЯ =====
    def fill_missing_expiration_dates():
        """Находит строки, где есть дата нарушения, но срок погашения пуст, и проставляет формулу."""
        # Определяем индексы столбцов
        date_col = None
        exp_col = None
        for i, col_name in enumerate(header_row):
            if 'дата нарушения' in col_name.lower():
                date_col = i
            if 'срок погашения' in col_name.lower():
                exp_col = i
        if date_col is None or exp_col is None:
            print("⚠️ Не удалось найти столбцы 'Дата нарушения' или 'Срок погашения' для заполнения.")
            return

        # Проходим по строкам, начиная со второй
        rows_to_update = []
        for row_num, row in enumerate(all_values[1:], start=2):
            date_val = row[date_col] if date_col < len(row) else ''
            exp_val = row[exp_col] if exp_col < len(row) else ''
            if date_val and not exp_val:
                # Проверим, что дата валидна
                try:
                    datetime.strptime(date_val, '%Y-%m-%d')
                    # Формируем формулу
                    date_cell = f"{chr(65 + date_col)}{row_num}"
                    formula = f"={date_cell}+21"
                    rows_to_update.append((row_num, exp_col, formula))
                except ValueError:
                    pass

        if rows_to_update:
            print(f"📝 Найдено {len(rows_to_update)} строк с пропущенным сроком погашения. Заполняем...")
            for row_num, exp_col, formula in rows_to_update:
                cell = f"{chr(65 + exp_col)}{row_num}"
                try:
                    sheet.update(range_name=cell, values=[[formula]], value_input_option='USER_ENTERED')
                except Exception as e:
                    print(f"⚠️ Ошибка при обновлении ячейки {cell}: {e}")
            print(f"✅ Заполнено {len(rows_to_update)} ячеек.")
        else:
            print("ℹ️ Пропущенных сроков погашения не найдено.")

    fill_missing_expiration_dates()

    # ===== УСЛОВНОЕ ФОРМАТИРОВАНИЕ ДЛЯ СРОКА ПОГАШЕНИЯ (красный активно, зелёный истекло, белый текст) =====
    def setup_conditional_formatting():
        expiration_col = None
        for i, col_name in enumerate(header_row):
            if 'срок погашения' in col_name.lower():
                expiration_col = i
                break
        if expiration_col is None:
            print("⚠️ Столбец 'Срок погашения' не найден, условное форматирование пропущено.")
            return

        body = {
            "requests": [
                {
                    "addConditionalFormatRule": {
                        "rule": {
                            "ranges": [
                                {
                                    "sheetId": sheet.id,
                                    "startRowIndex": 1,
                                    "startColumnIndex": expiration_col,
                                    "endColumnIndex": expiration_col + 1
                                }
                            ],
                            "booleanRule": {
                                "condition": {
                                    "type": "DATE_AFTER",
                                    "values": [{"userEnteredValue": "=TODAY()"}]
                                },
                                "format": {
                                    "backgroundColor": {"red": 0.8, "green": 0.2, "blue": 0.2},
                                    "textFormat": {
                                        "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}  # белый текст
                                    }
                                }
                            }
                        },
                        "index": 0
                    }
                },
                {
                    "addConditionalFormatRule": {
                        "rule": {
                            "ranges": [
                                {
                                    "sheetId": sheet.id,
                                    "startRowIndex": 1,
                                    "startColumnIndex": expiration_col,
                                    "endColumnIndex": expiration_col + 1
                                }
                            ],
                            "booleanRule": {
                                "condition": {
                                    "type": "DATE_BEFORE",
                                    "values": [{"userEnteredValue": "=TODAY()"}]
                                },
                                "format": {
                                    "backgroundColor": {"red": 0.2, "green": 0.8, "blue": 0.2},
                                    "textFormat": {
                                        "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}  # белый текст
                                    }
                                }
                            }
                        },
                        "index": 1
                    }
                }
            ]
        }
        try:
            sheet.spreadsheet.batch_update(body)
            print("✅ Условное форматирование для столбца 'Срок погашения' настроено (красный - активно, зелёный - истекло, текст белый).")
        except Exception as e:
            print(f"⚠️ Ошибка при настройке условного форматирования: {e}")

    setup_conditional_formatting()
    print("=== ДИАГНОСТИКА ЗАВЕРШЕНА ===")

except Exception as e:
    print(f"❌ Ошибка подключения: {e}")
    sys.exit(1)

# ===================== НАСТРОЙКА БОТА =====================
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

# ===================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====================
def get_current_records_with_rows():
    all_values = sheet.get_all_values()
    if len(all_values) <= 1:
        return []
    header_row = all_values[0]
    col_idx = {}
    for i, col_name in enumerate(header_row):
        normalized = col_name.strip().lower()
        col_idx[normalized] = i
    records = []
    for row_num, row in enumerate(all_values[1:], start=2):
        if not any(row):
            continue
        rec = {'row_num': row_num}
        for col_name, idx in col_idx.items():
            rec[col_name] = row[idx] if idx < len(row) else ''
        records.append(rec)
    return records

def find_column_index_by_name(col_name_pattern):
    header_row = sheet.get_all_values()[0]
    for i, header in enumerate(header_row):
        if col_name_pattern.lower() in header.lower().strip():
            return i
    return None

def get_last_nonempty_row():
    all_vals = sheet.get_all_values()
    for i in range(len(all_vals)-1, -1, -1):
        if any(all_vals[i]):
            return i + 1
    return 1

def format_row(sheet_obj, row_index_1based):
    all_vals = sheet_obj.get_all_values()
    if row_index_1based > len(all_vals):
        return
    cols = max(len(row) for row in all_vals) if all_vals else 0
    if cols == 0:
        return
    body = {
        "requests": [
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_obj.id,
                        "startRowIndex": row_index_1based - 1,
                        "endRowIndex": row_index_1based,
                        "startColumnIndex": 0,
                        "endColumnIndex": cols
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {
                                "fontFamily": "Arial",
                                "fontSize": 12
                            }
                        }
                    },
                    "fields": "userEnteredFormat.textFormat(fontFamily,fontSize)"
                }
            }
        ]
    }
    sheet_obj.spreadsheet.batch_update(body)

# ===================== ФУНКЦИЯ ПОИСКА АКТИВНЫХ НАРУШЕНИЙ (ТОЛЬКО ПО НИКУ) =====================
def get_active_punishments(nick):
    """Возвращает список активных нарушений (срок погашения >= сегодня) для данного ника."""
    records = get_current_records_with_rows()
    if not records:
        print("[DEBUG] Нет записей в таблице.")
        return []

    print("[DEBUG] Заголовки таблицы:", list(records[0].keys()))

    nick_key = None
    exp_key = None
    for key in records[0].keys():
        if 'ник' in key.lower():
            nick_key = key
        if 'срок погашения' in key.lower():
            exp_key = key

    print(f"[DEBUG] Найден ключ для ника: {nick_key}, для срока погашения: {exp_key}")

    all_nicks = [rec.get(nick_key, '') for rec in records if nick_key in rec]
    print(f"[DEBUG] Все ники в таблице: {all_nicks}")

    if nick_key is None or exp_key is None:
        print("[DEBUG] Не удалось найти столбцы 'Ник' или 'Срок погашения'.")
        return []

    active = []
    today = datetime.now().date()
    for rec in records:
        if rec.get(nick_key, '').lower() == nick.lower():
            exp_str = rec.get(exp_key, '').strip()
            print(f"[DEBUG] Нарушение: {rec}, Срок погашения: '{exp_str}'")
            if not exp_str:
                continue
            parsed = None
            for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%Y/%m/%d', '%d/%m/%Y', '%Y-%m-%d %H:%M:%S', '%d.%m.%Y %H:%M:%S'):
                try:
                    parsed = datetime.strptime(exp_str, fmt).date()
                    break
                except ValueError:
                    continue
            if parsed is None:
                print(f"[DEBUG] Не удалось распарсить дату: '{exp_str}'")
                continue
            if parsed >= today:
                active.append(rec)
                print(f"[DEBUG] Нарушение активно: {rec}")
    print(f"[DEBUG] Итого активных нарушений для {nick}: {len(active)}")
    return active

# ===================== МОДАЛЬНОЕ ОКНО ДЛЯ ДОБАВЛЕНИЯ =====================
class AddModal(ui.Modal, title='➕ Добавление нарушения'):
    def __init__(self, who_issued: str, rank: str):
        super().__init__()
        self.who_issued = who_issued
        self.rank = rank

    nick = ui.TextInput(label='Ник нарушителя', placeholder='Введите ник', required=True)
    date = ui.TextInput(label='Дата нарушения (ДД.ММ.ГГГГ)', placeholder='Например: 19.06.2026', required=True)
    violation = ui.TextInput(label='Вид нарушения (пункт правил)', placeholder='Например: 4.1 ОПС', required=True)
    seconds = ui.TextInput(label='Мера наказания (сек.)', placeholder='Только число', required=True)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_guild_only(interaction) or not is_allowed(interaction.user.id):
            await interaction.response.send_message('❌ Доступ запрещён.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        try:
            date_obj = datetime.strptime(self.date.value, '%d.%m.%Y')
            date_str = date_obj.strftime('%Y-%m-%d')
        except ValueError:
            await interaction.followup.send('❌ Неверный формат даты. Используйте ДД.ММ.ГГГГ', ephemeral=True)
            return

        try:
            seconds_int = int(self.seconds.value)
        except ValueError:
            await interaction.followup.send('❌ Мера наказания должна быть числом!', ephemeral=True)
            return

        nick = self.nick.value.strip()
        active = get_active_punishments(nick)

        if not active:
            await interaction.followup.send(f'ℹ️ Активных нарушений для **{nick}** не найдено. Нарушение будет добавлено без учёта рецидива.', ephemeral=True)
            await self.insert_punishment(interaction, nick, date_str, self.violation.value, seconds_int, date_obj, '', '')
            return

        lines = []
        for i, rec in enumerate(active, start=1):
            violation = rec.get('вид нарушения', '')
            date = rec.get('дата нарушения', '')
            expiration = rec.get('срок погашения', '')
            lines.append(f"**{i}.** {violation} (дата: {date}, срок: {expiration})")
        list_msg = "\n".join(lines)

        prompt = f"🔍 Найдены активные нарушения для **{nick}**:\n\n{list_msg}\n\nВведите номера нарушений через запятую (например, `1,3`), которые нужно учесть как **рецидив** и **предыдущие нарушения**.\nЕсли не нужно учитывать ни одно – введите `0` или `нет`."

        await interaction.followup.send(prompt, ephemeral=True)

        def check(m):
            return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id

        try:
            msg = await bot.wait_for('message', timeout=120.0, check=check)
        except TimeoutError:
            await interaction.followup.send('⏰ Время ожидания истекло. Попробуйте снова.', ephemeral=True)
            return

        selected_indices = []
        raw = msg.content.strip()
        if raw.lower() in ('0', 'нет', 'no'):
            selected_indices = []
        else:
            parts = [p.strip() for p in raw.split(',') if p.strip()]
            for part in parts:
                try:
                    num = int(part)
                    if 1 <= num <= len(active):
                        selected_indices.append(num)
                except ValueError:
                    pass
            if not selected_indices:
                await interaction.followup.send('❌ Неверный ввод. Попробуйте снова.', ephemeral=True)
                return

        recidivism_dates = []
        prev_violations = []
        for idx in selected_indices:
            rec = active[idx-1]
            recidivism_dates.append(rec.get('дата нарушения', ''))
            prev_violations.append(rec.get('вид нарушения', ''))

        recidivism_str = ', '.join(recidivism_dates)
        prev_violations_str = ', '.join(prev_violations)

        await self.insert_punishment(interaction, nick, date_str, self.violation.value, seconds_int, date_obj,
                                     recidivism_str, prev_violations_str)

    async def insert_punishment(self, interaction, nick, date_str, violation, seconds_int, date_obj,
                                recidivism_str, prev_violations_str):
        col_indices = {}
        for pattern in ['кем выдано', 'ник', 'звание', 'дата нарушения', 'вид нарушения',
                        'мера наказания (сек.)', 'срок погашения', 'рецидив', 'предыдущие нарушения']:
            idx = find_column_index_by_name(pattern)
            if idx is not None:
                col_indices[pattern] = idx

        header_row = sheet.get_all_values()[0]
        row = [''] * len(header_row)

        mapping = {
            'кем выдано': self.who_issued,
            'ник': nick,
            'звание': self.rank,
            'дата нарушения': date_str,
            'вид нарушения': violation,
            'мера наказания (сек.)': str(seconds_int),
            'рецидив': recidivism_str,
            'предыдущие нарушения': prev_violations_str
        }
        for key, value in mapping.items():
            if key in col_indices:
                row[col_indices[key]] = value

        if 'срок погашения' in col_indices:
            row[col_indices['срок погашения']] = ''

        last_row = get_last_nonempty_row()
        insert_pos = last_row + 1

        try:
            sheet.insert_row(row, index=insert_pos, value_input_option='USER_ENTERED')

            if 'дата нарушения' in col_indices and 'срок погашения' in col_indices:
                date_col = col_indices['дата нарушения']
                expiration_col = col_indices['срок погашения']
                date_cell = f"{chr(65 + date_col)}{insert_pos}"
                formula_a1 = f"={date_cell}+21"
                sheet.update(range_name=f"{chr(65 + expiration_col)}{insert_pos}",
                             values=[[formula_a1]],
                             value_input_option='USER_ENTERED')

            format_row(sheet, insert_pos)
            await interaction.followup.send(f'✅ Нарушение для **{nick}** добавлено!', ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f'❌ Ошибка: {e}', ephemeral=True)

# ===================== МОДАЛЬНОЕ ОКНО ДЛЯ ПОИСКА =====================
class FindModal(ui.Modal, title='🔍 Поиск нарушений по нику'):
    nick = ui.TextInput(label='Ник нарушителя', placeholder='Введите ник', required=True)
    async def on_submit(self, interaction: discord.Interaction):
        if not is_guild_only(interaction) or not is_allowed(interaction.user.id):
            await interaction.response.send_message('❌ Доступ запрещён.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            records = get_current_records_with_rows()
            if not records:
                await interaction.followup.send('Таблица пуста.', ephemeral=True)
                return
            header_row = sheet.get_all_values()[0]
            def find_col(pattern):
                for i, h in enumerate(header_row):
                    if pattern.lower() in h.lower().strip():
                        return i
                return None
            nick_idx = find_col('ник')
            violation_idx = find_col('вид нарушения')
            seconds_idx = find_col('мера наказания')
            date_idx = find_col('дата нарушения')
            rank_idx = find_col('звание')
            who_idx = find_col('кем выдано')
            additional_idx = find_col('дополнительные решения')
            if additional_idx is None:
                additional_idx = find_col('примечания')
            if nick_idx is None:
                await interaction.followup.send('❌ Столбец "Ник" не найден.', ephemeral=True)
                return
            search_term = self.nick.value.lower()
            found = []
            for rec in records:
                row_num = rec['row_num']
                row_data = sheet.row_values(row_num)
                nick_val = row_data[nick_idx] if nick_idx < len(row_data) else ''
                if search_term in nick_val.lower():
                    found.append({
                        'row_num': row_num,
                        'nick': nick_val,
                        'violation': row_data[violation_idx] if violation_idx is not None and violation_idx < len(row_data) else '',
                        'seconds': row_data[seconds_idx] if seconds_idx is not None and seconds_idx < len(row_data) else '',
                        'date': row_data[date_idx] if date_idx is not None and date_idx < len(row_data) else '',
                        'rank': row_data[rank_idx] if rank_idx is not None and rank_idx < len(row_data) else '',
                        'who': row_data[who_idx] if who_idx is not None and who_idx < len(row_data) else '',
                        'additional': row_data[additional_idx] if additional_idx is not None and additional_idx < len(row_data) else ''
                    })
            if not found:
                await interaction.followup.send(f'Ники, содержащие **{self.nick.value}**, не найдены.', ephemeral=True)
                return
            msg = f'**Нарушения для ников, содержащих "{self.nick.value}":**\n'
            for rec in found[:5]:
                msg += f'• Строка {rec["row_num"]}: {rec["nick"]}'
                if rec["rank"]:
                    msg += f' (Звание: {rec["rank"]})'
                if rec["who"]:
                    msg += f', выдал: {rec["who"]}'
                msg += f' — {rec["violation"]} — {rec["seconds"]} сек., дата: {rec["date"]}'
                if rec["additional"]:
                    msg += f' [Доп.: {rec["additional"]}]'
                msg += '\n'
            if len(found) > 5:
                msg += f'… и ещё {len(found)-5} записей.'
            await interaction.followup.send(msg, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f'❌ Ошибка: {e}', ephemeral=True)

# ===================== МОДАЛЬНОЕ ОКНО ДЛЯ ИЗМЕНЕНИЯ =====================
class EditModal(ui.Modal, title='✏️ Изменение строки'):
    row_num = ui.TextInput(label='Номер строки (первая запись = 2)', placeholder='Введите номер', required=True)
    nick = ui.TextInput(label='Новый ник (оставьте пустым, если не менять)', required=False)
    violation = ui.TextInput(label='Новый вид нарушения', required=False)
    seconds = ui.TextInput(label='Новая мера (сек.)', required=False)
    additional = ui.TextInput(
        label='Новые примечания / доп. информация',
        placeholder='Введите новые примечания (если нужно)',
        required=False,
        style=discord.TextStyle.paragraph
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not is_guild_only(interaction) or not is_allowed(interaction.user.id):
            await interaction.response.send_message('❌ Доступ запрещён.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            row_idx = int(self.row_num.value)
            if row_idx < 2:
                await interaction.followup.send('❌ Номер строки должен быть ≥ 2.', ephemeral=True)
                return
        except ValueError:
            await interaction.followup.send('❌ Номер строки должен быть числом.', ephemeral=True)
            return
        try:
            all_values = sheet.get_all_values()
            if row_idx > len(all_values):
                await interaction.followup.send('❌ Строка не найдена.', ephemeral=True)
                return
            existing_row = all_values[row_idx - 1]
            header_row = all_values[0]
            def find_idx(pattern):
                for i, h in enumerate(header_row):
                    if pattern.lower() in h.lower().strip():
                        return i
                return None
            col_indices = {
                'ник': find_idx('ник'),
                'вид нарушения': find_idx('вид нарушения'),
                'мера наказания (сек.)': find_idx('мера наказания'),
                'дата нарушения': find_idx('дата нарушения'),
                'срок погашения': find_idx('срок погашения'),
                'примечания': find_idx('примечания'),
                'дополнительные решения': find_idx('дополнительные решения')
            }
            new_row = existing_row[:]
            if len(new_row) < len(header_row):
                new_row.extend([''] * (len(header_row) - len(new_row)))
            if self.nick.value and col_indices['ник'] is not None:
                new_row[col_indices['ник']] = self.nick.value
            if self.violation.value and col_indices['вид нарушения'] is not None:
                new_row[col_indices['вид нарушения']] = self.violation.value
            if self.seconds.value and col_indices['мера наказания (сек.)'] is not None:
                try:
                    sec = int(self.seconds.value)
                    new_row[col_indices['мера наказания (сек.)']] = str(sec)
                    if col_indices['дата нарушения'] is not None and col_indices['срок погашения'] is not None:
                        date_cell = f"{chr(65 + col_indices['дата нарушения'])}{row_idx}"
                        formula_a1 = f"={date_cell}+21"
                        new_row[col_indices['срок погашения']] = formula_a1
                except ValueError:
                    await interaction.followup.send('❌ Мера наказания должна быть числом.', ephemeral=True)
                    return
            if self.additional.value:
                if col_indices['примечания'] is not None:
                    new_row[col_indices['примечания']] = self.additional.value
                elif col_indices['дополнительные решения'] is not None:
                    new_row[col_indices['дополнительные решения']] = self.additional.value

            cell_range = f'A{row_idx}:{chr(65 + len(header_row) - 1)}{row_idx}'
            sheet.update(range_name=cell_range,
                         values=[new_row],
                         value_input_option='USER_ENTERED')
            format_row(sheet, row_idx)
            await interaction.followup.send(f'✅ Строка {row_idx} обновлена!', ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f'❌ Ошибка: {e}', ephemeral=True)

# ===================== ВИДЫ ДЛЯ ПОШАГОВОГО ДИАЛОГА =====================
class WhoIssuedView(ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @ui.button(label='ВП', style=discord.ButtonStyle.primary)
    async def vp_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.process_choice(interaction, 'ВП')

    @ui.button(label='Администрация', style=discord.ButtonStyle.secondary)
    async def adm_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.process_choice(interaction, 'Администрация')

    async def process_choice(self, interaction: discord.Interaction, choice: str):
        if not is_allowed(interaction.user.id) or not is_guild_only(interaction):
            await interaction.response.send_message('❌ Доступ запрещён.', ephemeral=True)
            return
        view = RankSelectView(choice)
        embed = discord.Embed(
            title='Шаг 2: Выберите звание',
            description='Выберите звание нарушителя из списка:',
            color=discord.Color.blue()
        )
        await interaction.response.edit_message(embed=embed, view=view)

class RankSelectView(ui.View):
    RANKS = [
        'Штрафник',
        'Новобранец', 'Рядовой', 'Ефрейтор', 'Мл. Сержант', 'Сержант',
        'Ст. Сержант', 'Старшина', 'Прапорщик', 'Ст. Прапорщик',
        'Мл. Лейтенант', 'Лейтенант', 'Ст. Лейтенант', 'Капитан',
        'Майор', 'Подполковник', 'Полковник'
    ]

    def __init__(self, who_issued: str):
        super().__init__(timeout=300)
        self.who_issued = who_issued
        options = [discord.SelectOption(label=rank, value=rank) for rank in self.RANKS]
        select = ui.Select(placeholder='Выберите звание...', options=options, custom_id='rank_select')
        select.callback = self.rank_callback
        self.add_item(select)

    async def rank_callback(self, interaction: discord.Interaction):
        selected_rank = interaction.data['values'][0]
        await interaction.response.send_modal(AddModal(self.who_issued, selected_rank))

# ===================== ИСТОРИЯ (без кнопки закрытия) =====================
class HistoryView(ui.View):
    def __init__(self, records, page=0, per_page=5):
        super().__init__(timeout=120)
        self.records = records
        self.page = page
        self.per_page = per_page
        self.max_page = max(0, (len(records) - 1) // per_page)
        self.header_row = sheet.get_all_values()[0]
        self.nick_idx = self._find_col('ник')
        self.who_idx = self._find_col('кем выдано')
        self.rank_idx = self._find_col('звание')
        self.violation_idx = self._find_col('вид нарушения')
        self.date_idx = self._find_col('дата нарушения')
        self.additional_idx = self._find_col('дополнительные решения')
        if self.additional_idx is None:
            self.additional_idx = self._find_col('примечания')

    def _find_col(self, pattern):
        for i, h in enumerate(self.header_row):
            if pattern.lower() in h.lower().strip():
                return i
        return None

    def get_embed(self):
        start = self.page * self.per_page
        end = start + self.per_page
        page_records = self.records[start:end]
        embed = discord.Embed(
            title=f"📜 Последние записи (страница {self.page+1}/{self.max_page+1})",
            color=discord.Color.blue()
        )
        if not page_records:
            embed.description = "Нет записей."
            return embed
        for rec in page_records:
            row_num = rec['row_num']
            row_data = sheet.row_values(row_num)
            nick = row_data[self.nick_idx] if self.nick_idx is not None and self.nick_idx < len(row_data) else ''
            who = row_data[self.who_idx] if self.who_idx is not None and self.who_idx < len(row_data) else ''
            rank = row_data[self.rank_idx] if self.rank_idx is not None and self.rank_idx < len(row_data) else ''
            violation = row_data[self.violation_idx] if self.violation_idx is not None and self.violation_idx < len(row_data) else ''
            date = row_data[self.date_idx] if self.date_idx is not None and self.date_idx < len(row_data) else ''
            additional = row_data[self.additional_idx] if self.additional_idx is not None and self.additional_idx < len(row_data) else ''
            line = f"**{nick}**"
            if rank:
                line += f" (Звание: {rank})"
            if who:
                line += f" — выдал: {who}"
            line += f" — {violation} — {date}"
            if additional:
                line += f" [Доп.: {additional}]"
            embed.add_field(name=f"Строка {row_num}", value=line, inline=False)
        return embed

    @ui.button(label='◀️', style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: ui.Button):
        if self.page > 0:
            self.page -= 1
            await interaction.response.edit_message(embed=self.get_embed(), view=self)
        else:
            await interaction.response.defer()

    @ui.button(label='▶️', style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: ui.Button):
        if self.page < self.max_page:
            self.page += 1
            await interaction.response.edit_message(embed=self.get_embed(), view=self)
        else:
            await interaction.response.defer()

# ===================== ОСНОВНОЕ МЕНЮ =====================
class MenuView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label='➕ Добавить', style=discord.ButtonStyle.green)
    async def add_button(self, interaction: discord.Interaction, button: ui.Button):
        if not is_guild_only(interaction) or not is_allowed(interaction.user.id):
            await interaction.response.send_message('❌ Доступ запрещён.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        embed = discord.Embed(
            title='Шаг 1: Кем выдано наказание?',
            description='Выберите один из вариантов:',
            color=discord.Color.blue()
        )
        await interaction.followup.send(embed=embed, view=WhoIssuedView(), ephemeral=True)

    @ui.button(label='🔍 Найти', style=discord.ButtonStyle.blurple)
    async def find_button(self, interaction: discord.Interaction, button: ui.Button):
        if not is_guild_only(interaction) or not is_allowed(interaction.user.id):
            await interaction.response.send_message('❌ Доступ запрещён.', ephemeral=True)
            return
        await interaction.response.send_modal(FindModal())

    @ui.button(label='📜 История', style=discord.ButtonStyle.blurple)
    async def history_button(self, interaction: discord.Interaction, button: ui.Button):
        if not is_guild_only(interaction) or not is_allowed(interaction.user.id):
            await interaction.response.send_message('❌ Доступ запрещён.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            records = get_current_records_with_rows()
            if not records:
                await interaction.followup.send('Таблица пуста.', ephemeral=True)
                return
            records.sort(key=lambda x: x['row_num'], reverse=True)
            records = records[:20]
            view = HistoryView(records)
            await interaction.followup.send(embed=view.get_embed(), view=view, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f'❌ Ошибка: {e}', ephemeral=True)

    @ui.button(label='✏️ Изменить', style=discord.ButtonStyle.red)
    async def edit_button(self, interaction: discord.Interaction, button: ui.Button):
        if not is_guild_only(interaction) or not is_allowed(interaction.user.id):
            await interaction.response.send_message('❌ Доступ запрещён.', ephemeral=True)
            return
        await interaction.response.send_modal(EditModal())

# ===================== КОМАНДА МЕНЮ =====================
@bot.command(name='меню')
@commands.guild_only()
async def menu_command(ctx):
    if not is_allowed(ctx.author.id):
        await ctx.send('❌ У вас нет доступа к этому боту.')
        return
    embed = discord.Embed(
        title='📋 Панель управления нарушениями',
        description='Нажмите на кнопку, чтобы выполнить действие:',
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed, view=MenuView())

# ===================== АВТОМАТИЧЕСКОЕ ОБНОВЛЕНИЕ МЕНЮ =====================
async def send_or_update_menu():
    channel = bot.get_channel(CHANNEL_ID_INT)
    if not channel:
        print(f"⚠️ Канал {CHANNEL_ID_INT} не найден. Проверьте ID.")
        return

    embed = discord.Embed(
        title='📋 Панель управления нарушениями',
        description='Нажмите на кнопку, чтобы выполнить действие:',
        color=discord.Color.blue()
    )
    view = MenuView()

    try:
        found_msg = None
        async for msg in channel.history(limit=20):
            if msg.author.id == bot.user.id and msg.embeds:
                for emb in msg.embeds:
                    if emb.title == '📋 Панель управления нарушениями':
                        found_msg = msg
                        break
                if found_msg:
                    break

        if found_msg:
            await found_msg.edit(embed=embed, view=view)
            print(f"✅ Меню обновлено (сообщение {found_msg.id})")
        else:
            new_msg = await channel.send(embed=embed, view=view)
            print(f"✅ Меню отправлено (новое сообщение {new_msg.id})")
    except discord.Forbidden:
        print("❌ Нет прав для отправки/редактирования сообщений в канале.")
    except Exception as e:
        print(f"❌ Ошибка при отправке/обновлении меню: {e}")

@bot.event
async def on_ready():
    print(f'✅ Бот {bot.user} запущен!')
    await send_or_update_menu()

# ===================== ЗАПУСК =====================
if __name__ == '__main__':
    bot.run(TOKEN)
