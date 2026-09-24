import os
import asyncio
from io import BytesIO

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.types import BufferedInputFile, Message
from aiogram.filters import CommandStart
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


# ====== НАСТРОЙКИ ======
BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Переменная TELEGRAM_TOKEN не задана.")

MASTER_CHAT_ID = int(os.environ.get("MASTER_CHAT_ID", 6897048593))
SELF_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://127.0.0.1:10000")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{SELF_URL}{WEBHOOK_PATH}"
# =======================


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


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
    if message.from_user.id == MASTER_CHAT_ID:
        await message.answer("Ты мастер. Все заказы с сайта будут приходить сюда.")
    else:
        await message.answer("Этот бот принимает заказы только для мастера.")


# ====== Эндпоинт для сайта ======
async def handle_order(request: web.Request) -> web.Response:
    reader = await request.multipart()

    client_info = "Клиент без имени"
    text = ""
    file_bytes = None
    file_name = None

    async for part in reader:
        if part.name == "client":
            client_info = (await part.text()).strip() or client_info
        elif part.name == "content":
            text = (await part.text()).strip()
        elif part.name == "file":
            file_name = part.filename
            file_bytes = await part.read()

    filename = "order.pdf"

    if file_bytes and file_name and file_name.lower().endswith(".docx"):
        tmp_path = f"tmp_{file_name}"
        with open(tmp_path, "wb") as f:
            f.write(file_bytes)
        try:
            text = docx_to_text(tmp_path)
        finally:
            os.remove(tmp_path)
        filename = file_name.rsplit(".", 1)[0] + ".pdf"

    if not text:
        return web.json_response({"ok": False, "error": "Пустой заказ"}, status=400)

    pdf_buffer = text_to_pdf(text)

    try:
        await bot.send_document(
            MASTER_CHAT_ID,
            BufferedInputFile(pdf_buffer.getvalue(), filename=filename),
            caption=f"Новый заказ\n{client_info}"
        )
    except Exception as e:
        print("Ошибка отправки:", e)
        return web.json_response({"ok": False, "error": str(e)}, status=500)

    return web.json_response({"ok": True})


async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="OK")


async def on_startup(app: web.Application):
    await bot.set_webhook(WEBHOOK_URL)
    print(f"Webhook установлен: {WEBHOOK_URL}")


async def on_shutdown(app: web.Application):
    await bot.delete_webhook()
    await bot.session.close()


def create_app() -> web.Application:
    app = web.Application()

    # Webhook для бота
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    # Эндпоинты для сайта
    app.router.add_post("/api/order", handle_order)
    app.router.add_get("/health", handle_health)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    web.run_app(create_app(), host="0.0.0.0", port=port)
