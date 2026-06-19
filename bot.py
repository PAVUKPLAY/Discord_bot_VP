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

    print("=== ДИАГНОСТИКА ЗАВЕРШЕНА ===")

except Exception as e:
    print(f"❌ Ошибка подключения: {e}")
    sys.exit(1)

# ===================== НАСТРОЙКА БОТА =====================
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

# ===================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====================
def get_current_records():
    all_values = sheet.get_all_values()
    if len(all_values) <= 1:
        return []
    header_row = all_values[0]
    col_idx = {}
    for i, col_name in enumerate(header_row):
        normalized = col_name.strip().lower()
        col_idx[normalized] = i
    records = []
    for row in all_values[1:]:
        if not any(row):
            continue
        rec = {}
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

        header_row = sheet.get_all_values()[0]
        row = [''] * len(header_row)

        mapping = {
            'кем выдано': self.who_issued,
            'ник': self.nick.value,
            'звание': self.rank,
            'дата нарушения': date_str,
            'вид нарушения': self.violation.value,
            'мера наказания (сек.)': str(seconds_int),
            'срок погашения': (date_obj + timedelta(seconds=seconds_int)).strftime('%Y-%m-%d')
        }

        for pattern, value in mapping.items():
            idx = find_column_index_by_name(pattern)
            if idx is not None:
                row[idx] = value
            else:
                print(f"⚠️ Столбец с шаблоном '{pattern}' не найден в заголовках.")

        try:
            sheet.append_row(row, value_input_option='USER_ENTERED')
            last_row = len(sheet.get_all_values())
            format_row(sheet, last_row)
            await interaction.followup.send(f'✅ Нарушение для **{self.nick.value}** добавлено!', ephemeral=True)
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

# ===================== ОСНОВНОЕ МЕНЮ (исправленное) =====================
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

    @ui.button(label='📋 Последнее', style=discord.ButtonStyle.grey)
    async def last_button(self, interaction: discord.Interaction, button: ui.Button):
        if not is_guild_only(interaction) or not is_allowed(interaction.user.id):
            await interaction.response.send_message('❌ Доступ запрещён.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            records = get_current_records()
            if not records:
                await interaction.followup.send('Таблица пуста.', ephemeral=True)
                return
            last = records[-1]
            row_num = len(records) + 1
            msg = f'**Последняя запись (строка {row_num}):**\n'
            for key, val in last.items():
                msg += f'**{key}:** {val}\n'
            await interaction.followup.send(msg, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f'❌ Ошибка: {e}', ephemeral=True)

    @ui.button(label='✏️ Изменить', style=discord.ButtonStyle.red)
    async def edit_button(self, interaction: discord.Interaction, button: ui.Button):
        if not is_guild_only(interaction) or not is_allowed(interaction.user.id):
            await interaction.response.send_message('❌ Доступ запрещён.', ephemeral=True)
            return
        await interaction.response.send_modal(EditModal())

# ===================== МОДАЛЬНЫЕ ОКНА ДЛЯ ПОИСКА И ИЗМЕНЕНИЯ =====================
class FindModal(ui.Modal, title='🔍 Поиск нарушений по нику'):
    nick = ui.TextInput(label='Ник нарушителя', placeholder='Введите ник', required=True)
    async def on_submit(self, interaction: discord.Interaction):
        if not is_guild_only(interaction) or not is_allowed(interaction.user.id):
            await interaction.response.send_message('❌ Доступ запрещён.', ephemeral=True)
            return
        try:
            records = get_current_records()
            found = []
            nick_col = None
            for col in records[0].keys():
                if 'ник' in col.lower():
                    nick_col = col
                    break
            if nick_col is None:
                await interaction.response.send_message('❌ Столбец "Ник" не найден.', ephemeral=True)
                return
            for idx, rec in enumerate(records, start=2):
                if rec.get(nick_col, '').lower() == self.nick.value.lower():
                    found.append((idx, rec))
            if not found:
                await interaction.response.send_message(f'Нарушений для **{self.nick.value}** не найдено.', ephemeral=True)
                return
            msg = f'**Нарушения для {self.nick.value}:**\n'
            for idx, rec in found[:5]:
                violation = rec.get('вид нарушения', 'не указано')
                seconds = rec.get('мера наказания (сек.)', '')
                date = rec.get('дата нарушения', '')
                msg += f'• Строка {idx}: {violation} — {seconds} сек., дата: {date}\n'
            if len(found) > 5:
                msg += f'… и ещё {len(found)-5} записей.'
            await interaction.response.send_message(msg, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f'❌ Ошибка: {e}', ephemeral=True)

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

            col_indices = {}
            col_indices['ник'] = find_idx('ник')
            col_indices['вид нарушения'] = find_idx('вид нарушения')
            col_indices['мера наказания (сек.)'] = find_idx('мера наказания')
            col_indices['дата нарушения'] = find_idx('дата нарушения')
            col_indices['срок погашения'] = find_idx('срок погашения')
            col_indices['примечания'] = find_idx('примечания')
            col_indices['дополнительные решения'] = find_idx('дополнительные решения')

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
                    if col_indices['дата нарушения'] is not None:
                        date_str = new_row[col_indices['дата нарушения']]
                        if date_str:
                            try:
                                dt = datetime.strptime(date_str, '%Y-%m-%d')
                                if col_indices['срок погашения'] is not None:
                                    new_row[col_indices['срок погашения']] = (dt + timedelta(seconds=sec)).strftime('%Y-%m-%d')
                            except ValueError:
                                pass
                except ValueError:
                    await interaction.followup.send('❌ Мера наказания должна быть числом.', ephemeral=True)
                    return
            if self.additional.value:
                if col_indices['примечания'] is not None:
                    new_row[col_indices['примечания']] = self.additional.value
                elif col_indices['дополнительные решения'] is not None:
                    new_row[col_indices['дополнительные решения']] = self.additional.value

            cell_range = f'A{row_idx}:{chr(65 + len(header_row) - 1)}{row_idx}'
            sheet.update(cell_range, [new_row], value_input_option='USER_ENTERED')
            format_row(sheet, row_idx)
            await interaction.followup.send(f'✅ Строка {row_idx} обновлена!', ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f'❌ Ошибка: {e}', ephemeral=True)

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
        print(f"⚠️ Канал {CHANNEL_ID_INT} не найден.")
        return

    embed = discord.Embed(
        title='📋 Панель управления нарушениями',
        description='Нажмите на кнопку, чтобы выполнить действие:',
        color=discord.Color.blue()
    )
    view = MenuView()

    try:
        async for msg in channel.history(limit=20):
            if msg.author.id == bot.user.id and msg.embeds:
                for emb in msg.embeds:
                    if emb.title == '📋 Панель управления нарушениями':
                        await msg.edit(embed=embed, view=view)
                        print(f"✅ Меню обновлено (сообщение {msg.id})")
                        return
        new_msg = await channel.send(embed=embed, view=view)
        print(f"✅ Меню отправлено (новое сообщение {new_msg.id})")
    except Exception as e:
        print(f"❌ Ошибка при отправке/обновлении меню: {e}")

@bot.event
async def on_ready():
    print(f'✅ Бот {bot.user} запущен!')
    await send_or_update_menu()

# ===================== ЗАПУСК =====================
if __name__ == '__main__':
    bot.run(TOKEN)
