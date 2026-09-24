import os
import json
import asyncio
import threading
from io import BytesIO

from flask import Flask, request, jsonify
from flask_cors import CORS

from aiogram import Bot, Dispatcher
from aiogram.types import BufferedInputFile, Message
from aiogram.filters import CommandStart

from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


# ====== НАСТРОЙКИ ======
# Токен и пароль берем из переменных окружения Render (или из кода для локального теста)
BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN", "ВСТАВЬ_СВОЙ_ТОКЕН_СЮДА")
PASSWORD = "ss11ss11"
# =======================


app = Flask(__name__)
CORS(app)

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Глобальная ссылка на event loop бота
bot_loop: asyncio.AbstractEventLoop | None = None
# Хранилище chat_id мастера (в памяти, чтобы не мучиться с файлами на хостинге)
MASTER_CHAT_ID: int | None = None


# ====== Генерация PDF ======
def text_to_pdf(text: str) -> BytesIO:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    margin_left = 50
    margin_top = height - 60
    line_height = 18
    max_width = width - 100

    c.setFont("Helvetica", 11)

    y = margin_top
    for paragraph in text.split("\n"):
        words = paragraph.split(" ")
        line = ""
        for word in words:
            test = line + word + " "
            if c.stringWidth(test, "Helvetica", 11) < max_width:
                line = test
            else:
                c.drawString(margin_left, y, line.strip())
                y -= line_height
                line = word + " "
                if y < 60:
                    c.showPage()
                    c.setFont("Helvetica", 11)
                    y = margin_top
        c.drawString(margin_left, y, line.strip())
        y -= line_height
        if y < 60:
            c.showPage()
            c.setFont("Helvetica", 11)
            y = margin_top

    c.save()
    buffer.seek(0)
    return buffer


def docx_to_text(path: str) -> str:
    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs)


# ====== Хендлер бота ======
@dp.message(CommandStart())
async def start(message: Message):
    await message.answer("Введи пароль.")


@dp.message(lambda m: m.text == PASSWORD)
async def check_password(message: Message):
    global MASTER_CHAT_ID
    MASTER_CHAT_ID = message.from_user.id
    await message.answer("Пароль верный. Теперь заказы с сайта будут приходить сюда.")


# ====== Отправка заказа мастеру ======
async def send_to_master(pdf_buffer: BytesIO, filename: str, client_info: str):
    global MASTER_CHAT_ID
    if not MASTER_CHAT_ID:
        print("Мастер ещё не ввёл пароль в боте — отправить некуда.")
        return

    pdf_buffer.name = filename
    await bot.send_document(
        MASTER_CHAT_ID,
        BufferedInputFile(pdf_buffer.getvalue(), filename=filename),
        caption=f"Новый заказ\n{client_info}"
    )


# ====== Эндпоинт для сайта ======
@app.route("/api/order", methods=["POST"])
def order():
    client_info = request.form.get("client", "Клиент без имени")
    text = request.form.get("content", "").strip()
    file = request.files.get("file")

    filename = "order.pdf"

    if file and file.filename.lower().endswith(".docx"):
        tmp_path = f"tmp_{file.filename}"
        file.save(tmp_path)
        try:
            text = docx_to_text(tmp_path)
        finally:
            os.remove(tmp_path)
        filename = file.filename.rsplit(".", 1)[0] + ".pdf"

    if not text:
        return jsonify({"ok": False, "error": "Пустой заказ"}), 400

    pdf_buffer = text_to_pdf(text)

    # Отправляем задачу в event loop бота
    if bot_loop is None:
        return jsonify({"ok": False, "error": "Бот не запущен"}), 500

    future = asyncio.run_coroutine_threadsafe(
        send_to_master(pdf_buffer, filename, client_info),
        bot_loop
    )
    try:
        future.result(timeout=30)
    except Exception as e:
        print("Ошибка отправки:", e)
        return jsonify({"ok": False, "error": "Не удалось отправить в Telegram"}), 500

    return jsonify({"ok": True})


# ====== Главная страница (для проверки) ======
@app.route("/")
def index():
    return "SAJARI order server is running"


# ====== Health Check для Render (чтобы не засыпал) ======
@app.route("/health")
def health():
    return "OK", 200


# ====== Запуск бота ======
async def run_bot():
    global bot_loop
    bot_loop = asyncio.get_running_loop()
    await dp.start_polling(bot)


def start_bot_thread():
    asyncio.run(run_bot())


# ====== Запуск Flask ======
if __name__ == "__main__":
    # Запускаем бота в отдельном потоке
    threading.Thread(target=start_bot_thread, daemon=True).start()
    # Запускаем Flask на порту, который дает Render (или 5000 для локального теста)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
