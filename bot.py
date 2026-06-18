import discord
from discord import ui
from discord.ext import commands
import gspread
import os
import json
from datetime import datetime, timedelta

# ---------- КОНФИГУРАЦИЯ ----------
TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    raise ValueError("❌ Переменная DISCORD_TOKEN не установлена!")

SHEET_ID = 'ВАШ_ID_ТАБЛИЦЫ'   # ЗАМЕНИТЕ НА РЕАЛЬНЫЙ ID
if SHEET_ID == 'ВАШ_ID_ТАБЛИЦЫ':
    raise ValueError("❌ Укажите SHEET_ID в коде!")

# ---------- ПОДКЛЮЧЕНИЕ К GOOGLE SHEETS ----------
try:
    creds_json = os.getenv('GOOGLE_CREDENTIALS')
    if not creds_json:
        raise ValueError("❌ Переменная окружения GOOGLE_CREDENTIALS не найдена!")

    creds_dict = json.loads(creds_json)
    gc = gspread.service_account_from_dict(creds_dict)
    sheet = gc.open_by_key(SHEET_ID).sheet1
    print("✅ Подключение к Google Sheets успешно!")
except Exception as e:
    print(f"❌ Ошибка подключения к Google Sheets: {e}")
    exit(1)

# ---------- НАСТРОЙКА БОТА ----------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

# ---------- МОДАЛЬНОЕ ОКНО ДЛЯ ДОБАВЛЕНИЯ ----------
class AddModal(ui.Modal, title='➕ Добавление нарушения'):
    nick = ui.TextInput(label='Ник нарушителя', placeholder='Введите ник', required=True)
    violation = ui.TextInput(label='Вид нарушения', placeholder='Например: Гриферство', required=True)
    seconds = ui.TextInput(label='Мера наказания (сек.)', placeholder='Только число', required=True)
    rank = ui.TextInput(label='Звание (с 2496 строки)', required=False)
    recidivism = ui.TextInput(label='Рецидив', required=False)
    previous = ui.TextInput(label='Предыдущие нарушения', required=False)
    notes = ui.TextInput(label='Примечания', required=False)
    additional = ui.TextInput(label='Дополнительные решения', required=False)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            seconds_int = int(self.seconds.value)
        except ValueError:
            await interaction.response.send_message('❌ Мера наказания должна быть числом!', ephemeral=True)
            return

        now = datetime.now()
        who = interaction.user.name
        row = [
            who,                                   # Кем выдано
            self.nick.value,                       # Ник
            self.rank.value or '',                 # Звание
            now.strftime('%Y-%m-%d %H:%M:%S'),      # Дата нарушения
            self.violation.value,                  # Вид нарушения
            seconds_int,                           # Мера наказания (сек.)
            (now + timedelta(seconds=seconds_int)).strftime('%Y-%m-%d %H:%M:%S'),  # Срок погашения
            self.recidivism.value or '',           # Рецидив
            self.previous.value or '',             # Предыдущие нарушения
            self.notes.value or '',                # Примечания
            self.additional.value or ''            # Дополнительные решения
        ]
        try:
            sheet.append_row(row)
            await interaction.response.send_message(f'✅ Нарушение для **{self.nick.value}** добавлено!', ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f'❌ Ошибка: {e}', ephemeral=True)

# ---------- МОДАЛЬНОЕ ОКНО ДЛЯ ПОИСКА ----------
class FindModal(ui.Modal, title='🔍 Поиск нарушений по нику'):
    nick = ui.TextInput(label='Ник нарушителя', placeholder='Введите ник', required=True)

    async def on_submit(self, interaction: discord.Interaction):
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

# ---------- МОДАЛЬНОЕ ОКНО ДЛЯ ИЗМЕНЕНИЯ ----------
class EditModal(ui.Modal, title='✏️ Изменение строки'):
    row_num = ui.TextInput(label='Номер строки (первая запись = 2)', placeholder='Введите номер', required=True)
    nick = ui.TextInput(label='Новый ник (оставьте пустым, если не менять)', required=False)
    rank = ui.TextInput(label='Новое звание', required=False)
    violation = ui.TextInput(label='Новый вид нарушения', required=False)
    seconds = ui.TextInput(label='Новая мера (сек.)', required=False)
    recidivism = ui.TextInput(label='Новый рецидив', required=False)
    previous = ui.TextInput(label='Новые предыдущие нарушения', required=False)
    notes = ui.TextInput(label='Новые примечания', required=False)
    additional = ui.TextInput(label='Новые доп. решения', required=False)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            row_idx = int(self.row_num.value)
            if row_idx < 2:
                await interaction.response.send_message('❌ Номер строки должен быть ≥ 2 (первая строка – заголовки).', ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message('❌ Номер строки должен быть числом.', ephemeral=True)
            return

        try:
            existing = sheet.row_values(row_idx)
            if not existing:
                await interaction.response.send_message('❌ Строка не найдена.', ephemeral=True)
                return

            new_row = existing[:]  # копия

            if self.nick.value:
                new_row[1] = self.nick.value
            if self.rank.value:
                new_row[2] = self.rank.value
            if self.violation.value:
                new_row[4] = self.violation.value
            if self.seconds.value:
                try:
                    sec = int(self.seconds.value)
                    new_row[5] = sec
                    # Пересчёт срока погашения
                    violation_date_str = new_row[3]
                    if violation_date_str:
                        dt = datetime.strptime(violation_date_str, '%Y-%m-%d %H:%M:%S')
                        new_row[6] = (dt + timedelta(seconds=sec)).strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        new_row[6] = ''
                except ValueError:
                    await interaction.response.send_message('❌ Мера наказания должна быть числом.', ephemeral=True)
                    return
            if self.recidivism.value:
                new_row[7] = self.recidivism.value
            if self.previous.value:
                new_row[8] = self.previous.value
            if self.notes.value:
                new_row[9] = self.notes.value
            if self.additional.value:
                new_row[10] = self.additional.value

            sheet.update(f'A{row_idx}:K{row_idx}', [new_row])
            await interaction.response.send_message(f'✅ Строка {row_idx} успешно обновлена!', ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f'❌ Ошибка: {e}', ephemeral=True)

# ---------- КНОПКИ МЕНЮ ----------
class MenuView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # Кнопки работают постоянно

    @ui.button(label='➕ Добавить', style=discord.ButtonStyle.green)
    async def add_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(AddModal())

    @ui.button(label='🔍 Найти', style=discord.ButtonStyle.blurple)
    async def find_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(FindModal())

    @ui.button(label='📋 Последнее', style=discord.ButtonStyle.grey)
    async def last_button(self, interaction: discord.Interaction, button: ui.Button):
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
        await interaction.response.send_modal(EditModal())

# ---------- КОМАНДА ДЛЯ ОТПРАВКИ МЕНЮ ----------
@bot.command(name='меню')
async def menu_command(ctx):
    """Отправляет сообщение с кнопками меню"""
    embed = discord.Embed(
        title='📋 Панель управления нарушениями',
        description='Нажмите на кнопку, чтобы выполнить действие:',
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed, view=MenuView())

# ---------- ЗАПУСК БОТА ----------
@bot.event
async def on_ready():
    print(f'✅ Бот {bot.user} запущен!')
    # Можно также отправить приветственное сообщение в лог
    # await bot.get_channel(123456789).send('Бот запущен!')

if __name__ == '__main__':
    bot.run(TOKEN)
