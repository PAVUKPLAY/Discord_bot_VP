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
    test_data = sheet.get_all_records()
    print(f"✅ Доступ подтверждён (записей: {len(test_data)})")
    print("=== ДИАГНОСТИКА ЗАВЕРШЕНА ===")
except Exception as e:
    print(f"❌ Ошибка подключения: {e}")
    sys.exit(1)

# ===================== НАСТРОЙКА БОТА =====================
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

# ===================== МОДАЛЬНОЕ ОКНО ДЛЯ ДОБАВЛЕНИЯ (финальный шаг) =====================
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
        # Проверка доступа
        if not is_guild_only(interaction) or not is_allowed(interaction.user.id):
            await interaction.response.send_message('❌ Доступ запрещён.', ephemeral=True)
            return

        # Парсим дату
        try:
            date_obj = datetime.strptime(self.date.value, '%d.%m.%Y')
            date_str = date_obj.strftime('%Y-%m-%d')
        except ValueError:
            await interaction.response.send_message('❌ Неверный формат даты. Используйте ДД.ММ.ГГГГ', ephemeral=True)
            return

        # Парсим секунды
        try:
            seconds_int = int(self.seconds.value)
        except ValueError:
            await interaction.response.send_message('❌ Мера наказания должна быть числом!', ephemeral=True)
            return

        now = datetime.now()
        # Формируем строку для таблицы (порядок столбцов:
        # 0:Кем выдано, 1:Ник, 2:Звание, 3:Дата нарушения, 4:Вид нарушения,
        # 5:Мера наказания (сек.), 6:Срок погашения, 7:Рецидив, 8:Предыдущие,
        # 9:Примечания, 10:Дополнительные решения)
        row = [
            self.who_issued,                     # Кем выдано
            self.nick.value,                     # Ник
            self.rank,                           # Звание
            date_str,                            # Дата нарушения (без времени)
            self.violation.value,                # Вид нарушения
            seconds_int,                         # Мера наказания (сек.)
            (date_obj + timedelta(seconds=seconds_int)).strftime('%Y-%m-%d'),  # Срок погашения (дата)
            '',                                  # Рецидив
            '',                                  # Предыдущие нарушения
            '',                                  # Примечания
            ''                                   # Дополнительные решения
        ]
        try:
            sheet.append_row(row)
            await interaction.response.send_message(f'✅ Нарушение для **{self.nick.value}** добавлено!', ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f'❌ Ошибка: {e}', ephemeral=True)

# ===================== ВИДЫ ДЛЯ ПОШАГОВОГО ДИАЛОГА =====================
class WhoIssuedView(ui.View):
    """Первый шаг: выбор ВП или Адм"""
    def __init__(self):
        super().__init__(timeout=300)  # 5 минут на весь диалог

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
        # Создаём новый View для выбора звания и передаём choice
        view = RankSelectView(choice)
        embed = discord.Embed(
            title='Шаг 2: Выберите звание',
            description='Выберите звание нарушителя из списка:',
            color=discord.Color.blue()
        )
        await interaction.response.edit_message(embed=embed, view=view)

class RankSelectView(ui.View):
    """Второй шаг: выбор звания из выпадающего списка"""
    RANKS = [
        'Новобранец', 'Рядовой', 'Ефрейтор', 'Мл. Сержант', 'Сержант',
        'Ст. Сержант', 'Старшина', 'Прапорщик', 'Ст. Прапорщик',
        'Мл. Лейтенант', 'Лейтенант', 'Ст. Лейтенант', 'Капитан',
        'Майор', 'Подполковник', 'Полковник'
    ]

    def __init__(self, who_issued: str):
        super().__init__(timeout=300)
        self.who_issued = who_issued

        # Создаём селект
        options = [discord.SelectOption(label=rank, value=rank) for rank in self.RANKS]
        select = ui.Select(placeholder='Выберите звание...', options=options, custom_id='rank_select')
        select.callback = self.rank_callback
        self.add_item(select)

    async def rank_callback(self, interaction: discord.Interaction):
        selected_rank = interaction.data['values'][0]
        # Открываем модальное окно с остальными полями
        await interaction.response.send_modal(AddModal(self.who_issued, selected_rank))

# ===================== ОСНОВНОЕ МЕНЮ (4 кнопки) =====================
class MenuView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label='➕ Добавить', style=discord.ButtonStyle.green)
    async def add_button(self, interaction: discord.Interaction, button: ui.Button):
        if not is_guild_only(interaction) or not is_allowed(interaction.user.id):
            await interaction.response.send_message('❌ Доступ запрещён.', ephemeral=True)
            return
        # Начинаем диалог – отправляем эфемерное сообщение с выбором ВП/Адм
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
        # Здесь можно оставить FindModal, но для простоты пока оставляем старый диалог
        # Можно переделать аналогично, но пока оставим как есть
        # Для краткости оставим старый FindModal (его можно позже адаптировать)
        await interaction.response.send_modal(FindModal())

    @ui.button(label='📋 Последнее', style=discord.ButtonStyle.grey)
    async def last_button(self, interaction: discord.Interaction, button: ui.Button):
        if not is_guild_only(interaction) or not is_allowed(interaction.user.id):
            await interaction.response.send_message('❌ Доступ запрещён.', ephemeral=True)
            return
        try:
            records = sheet.get_all_records()
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
        # Пока оставляем старый EditModal (его тоже можно адаптировать позже)
        await interaction.response.send_modal(EditModal())

# ===================== МОДАЛЬНЫЕ ОКНА ДЛЯ ПОИСКА И ИЗМЕНЕНИЯ (остаются без изменений) =====================
class FindModal(ui.Modal, title='🔍 Поиск нарушений по нику'):
    nick = ui.TextInput(label='Ник нарушителя', placeholder='Введите ник', required=True)
    async def on_submit(self, interaction: discord.Interaction):
        if not is_guild_only(interaction) or not is_allowed(interaction.user.id):
            await interaction.response.send_message('❌ Доступ запрещён.', ephemeral=True)
            return
        try:
            records = sheet.get_all_records()
            found = []
            for idx, rec in enumerate(records, start=2):
                if rec.get('Ник', '').lower() == self.nick.value.lower():
                    found.append((idx, rec))
            if not found:
                await interaction.response.send_message(f'Нарушений для **{self.nick.value}** не найдено.', ephemeral=True)
                return
            msg = f'**Нарушения для {self.nick.value}:**\n'
            for idx, rec in found[:5]:
                msg += f'• Строка {idx}: {rec["Вид нарушения"]} — {rec["Мера наказания (сек.)"]} сек., дата: {rec["Дата нарушения"]}\n'
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
            existing = sheet.row_values(row_idx)
            if not existing:
                await interaction.response.send_message('❌ Строка не найдена.', ephemeral=True)
                return
            new_row = existing[:]
            if self.nick.value:
                new_row[1] = self.nick.value
            if self.violation.value:
                new_row[4] = self.violation.value
            if self.seconds.value:
                try:
                    sec = int(self.seconds.value)
                    new_row[5] = sec
                    # Пересчитываем срок погашения
                    if new_row[3]:
                        dt = datetime.strptime(new_row[3], '%Y-%m-%d')
                        new_row[6] = (dt + timedelta(seconds=sec)).strftime('%Y-%m-%d')
                    else:
                        new_row[6] = ''
                except ValueError:
                    await interaction.response.send_message('❌ Мера наказания должна быть числом.', ephemeral=True)
                    return
            if self.additional.value:
                new_row[9] = self.additional.value
            sheet.update(f'A{row_idx}:K{row_idx}', [new_row])
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
