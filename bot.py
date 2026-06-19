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

    # ===== АЛЬТЕРНАТИВНОЕ ЧТЕНИЕ ЗАГОЛОВКОВ И ДАННЫХ =====
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

    # ===== ПРИМЕНЕНИЕ ФОРМАТИРОВАНИЯ ARIAL 12 =====
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

    # ===== СОЗДАНИЕ РАСКРЫВАЮЩИХСЯ СПИСКОВ ДЛЯ СТОЛБЦОВ "Кем выдано" И "Звание" =====
    def create_dropdown(sheet_obj, col_name, options_list):
        # Ищем индекс столбца по имени
        col_idx = None
        for i, c in enumerate(header_row):
            if c.strip() == col_name:
                col_idx = i
                break
        if col_idx is None:
            print(f"⚠️ Столбец '{col_name}' не найден для создания списка.")
            return
        # Создаём правило проверки данных
        body = {
            "requests": [
                {
                    "setDataValidation": {
                        "range": {
                            "sheetId": sheet_obj.id,
                            "startRowIndex": 1,  # начиная со второй строки (первая — заголовок)
                            "endRowIndex": rows,  # до последней имеющейся строки
                            "startColumnIndex": col_idx,
                            "endColumnIndex": col_idx + 1
                        },
                        "rule": {
                            "condition": {
                                "type": "ONE_OF_LIST",
                                "values": [{"userEnteredValue": opt} for opt in options_list]
                            },
                            "showCustomUi": True,
                            "strict": True
                        }
                    }
                }
            ]
        }
        sheet_obj.spreadsheet.batch_update(body)
        print(f"✅ Раскрывающийся список для столбца '{col_name}' создан (вариантов: {len(options_list)})")

    # Список для "Кем выдано"
    who_options = ['ВП', 'Адм']
    create_dropdown(sheet, 'Кем выдано', who_options)

    # Список для "Звание"
    rank_options = [
        'Новобранец', 'Рядовой', 'Ефрейтор', 'Мл. Сержант', 'Сержант',
        'Ст. Сержант', 'Старшина', 'Прапорщик', 'Ст. Прапорщик',
        'Мл. Лейтенант', 'Лейтенант', 'Ст. Лейтенант', 'Капитан',
        'Майор', 'Подполковник', 'Полковник'
    ]
    create_dropdown(sheet, 'Звание', rank_options)

    print("=== ДИАГНОСТИКА ЗАВЕРШЕНА ===")

except Exception as e:
    print(f"❌ Ошибка подключения: {e}")
    sys.exit(1)

# ===================== НАСТРОЙКА БОТА =====================
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

# ===================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ РАБОТЫ С ТАБЛИЦЕЙ =====================
def get_current_records():
    all_values = sheet.get_all_values()
    if len(all_values) <= 1:
        return []
    header_row = all_values[0]
    col_idx = {}
    for i, col_name in enumerate(header_row):
        col_name = col_name.strip()
        if not col_name:
            col_name = f"Column{i+1}"
        if col_name in col_idx:
            suffix = 1
            while f"{col_name}_{suffix}" in col_idx:
                suffix += 1
            col_name = f"{col_name}_{suffix}"
        col_idx[col_name] = i
    records = []
    for row in all_values[1:]:
        if not any(row):
            continue
        rec = {}
        for col_name, idx in col_idx.items():
            rec[col_name] = row[idx] if idx < len(row) else ''
        records.append(rec)
    return records

def get_column_index():
    header_row = sheet.get_all_values()[0]
    col_idx = {}
    for i, col_name in enumerate(header_row):
        col_name = col_name.strip()
        if not col_name:
            col_name = f"Column{i+1}"
        if col_name in col_idx:
            suffix = 1
            while f"{col_name}_{suffix}" in col_idx:
                suffix += 1
            col_name = f"{col_name}_{suffix}"
        col_idx[col_name] = i
    return col_idx

def get_last_nonempty_row():
    """Возвращает номер последней строки (1-based), которая содержит какие-либо данные.
       Если данных нет (только заголовки), возвращает 1."""
    all_vals = sheet.get_all_values()
    if len(all_vals) <= 1:
        return 1  # только заголовок
    # Проверяем строки с конца
    for i in range(len(all_vals)-1, 0, -1):
        if any(all_vals[i]):
            return i + 1  # 1-based индекс следующей строки
    return 1  # если нет данных

def format_row(sheet_obj, row_index_1based):
    """Применяет Arial 12 к указанной строке (1-based)."""
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

        try:
            date_obj = datetime.strptime(self.date.value, '%d.%m.%Y')
            date_str = date_obj.strftime('%Y-%m-%d')
        except ValueError:
            await interaction.response.send_message('❌ Неверный формат даты. Используйте ДД.ММ.ГГГГ', ephemeral=True)
            return

        try:
            seconds_int = int(self.seconds.value)
        except ValueError:
            await interaction.response.send_message('❌ Мера наказания должна быть числом!', ephemeral=True)
            return

        col_idx = get_column_index()
        row = [''] * len(col_idx)
        mapping = {
            'Кем выдано': self.who_issued,
            'Ник': self.nick.value,
            'Звание': self.rank,
            'Дата нарушения': date_str,
            'Вид нарушения': self.violation.value,
            'Мера наказания (сек.)': str(seconds_int),
            'Срок погашения': (date_obj + timedelta(seconds=seconds_int)).strftime('%Y-%m-%d')
        }
        for col_name, value in mapping.items():
            if col_name in col_idx:
                row[col_idx[col_name]] = value

        # Находим последнюю непустую строку и вставляем после неё
        last_row = get_last_nonempty_row()
        insert_pos = last_row + 1  # следующая строка
        try:
            sheet.insert_row(row, index=insert_pos, value_input_option='USER_ENTERED')
            # Применяем форматирование к вставленной строке
            format_row(sheet, insert_pos)
            await interaction.response.send_message(f'✅ Нарушение для **{self.nick.value}** добавлено!', ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f'❌ Ошибка: {e}', ephemeral=True)

# ===================== ВИДЫ ДЛЯ ПОШАГОВОГО ДИАЛОГА =====================
class WhoIssuedView(ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @ui.button(label='ВП', style=discord.ButtonStyle.primary)
    async def vp_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.process_choice(interaction, 'ВП')

    @ui.button(label='Адм', style=discord.ButtonStyle.secondary)
    async def adm_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.process_choice(interaction, 'Адм')

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

# ===================== ОСНОВНОЕ МЕНЮ =====================
class MenuView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label='➕ Добавить', style=discord.ButtonStyle.green)
    async def add_button(self, interaction: discord.Interaction, button: ui.Button):
        if not is_guild_only(interaction) or not is_allowed(interaction.user.id):
            await interaction.response.send_message('❌ Доступ запрещён.', ephemeral=True)
            return
        embed = discord.Embed(
            title='Шаг 1: Кем выдано наказание?',
            description='Выберите один из вариантов:',
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, view=WhoIssuedView(), ephemeral=True)

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
        try:
            records = get_current_records()
            if not records:
                await interaction.response.send_message('Таблица пуста.', ephemeral=True)
                return
            last = records[-1]
            row_num = len(records) + 1
            msg = f'**Последняя запись (строка {row_num}):**\n'
            for key, val in last.items():
                msg += f'**{key}:** {val}\n'
            await interaction.response.send_message(msg, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f'❌ Ошибка: {e}', ephemeral=True)

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
                if 'Ник' in col:
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
                violation = rec.get('Вид нарушения', 'не указано')
                seconds = rec.get('Мера наказания (сек.)', '')
                date = rec.get('Дата нарушения', '')
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
        try:
            row_idx = int(self.row_num.value)
            if row_idx < 2:
                await interaction.response.send_message('❌ Номер строки должен быть ≥ 2.', ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message('❌ Номер строки должен быть числом.', ephemeral=True)
            return

        try:
            all_values = sheet.get_all_values()
            if row_idx > len(all_values):
                await interaction.response.send_message('❌ Строка не найдена.', ephemeral=True)
                return
            existing_row = all_values[row_idx - 1]
            col_idx = get_column_index()

            new_row = existing_row[:]
            if len(new_row) < len(col_idx):
                new_row.extend([''] * (len(col_idx) - len(new_row)))

            if self.nick.value:
                if 'Ник' in col_idx:
                    new_row[col_idx['Ник']] = self.nick.value
            if self.violation.value:
                if 'Вид нарушения' in col_idx:
                    new_row[col_idx['Вид нарушения']] = self.violation.value
            if self.seconds.value:
                try:
                    sec = int(self.seconds.value)
                    if 'Мера наказания (сек.)' in col_idx:
                        new_row[col_idx['Мера наказания (сек.)']] = str(sec)
                    if 'Дата нарушения' in col_idx and col_idx['Дата нарушения'] < len(new_row):
                        date_str = new_row[col_idx['Дата нарушения']]
                        if date_str:
                            try:
                                dt = datetime.strptime(date_str, '%Y-%m-%d')
                                if 'Срок погашения' in col_idx:
                                    new_row[col_idx['Срок погашения']] = (dt + timedelta(seconds=sec)).strftime('%Y-%m-%d')
                            except ValueError:
                                pass
                except ValueError:
                    await interaction.response.send_message('❌ Мера наказания должна быть числом.', ephemeral=True)
                    return
            if self.additional.value:
                if 'Примечания' in col_idx:
                    new_row[col_idx['Примечания']] = self.additional.value
                elif 'Дополнительные решения' in col_idx:
                    new_row[col_idx['Дополнительные решения']] = self.additional.value

            cell_range = f'A{row_idx}:{chr(65 + len(col_idx) - 1)}{row_idx}'
            sheet.update(cell_range, [new_row], value_input_option='USER_ENTERED')
            format_row(sheet, row_idx)
            await interaction.response.send_message(f'✅ Строка {row_idx} обновлена!', ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f'❌ Ошибка: {e}', ephemeral=True)

# ===================== КОМАНДА ДЛЯ ОТПРАВКИ МЕНЮ =====================
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
