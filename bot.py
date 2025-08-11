import os
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.types import Message, ContentType, CallbackQuery, InputMediaPhoto, FSInputFile, InlineKeyboardMarkup, \
    InlineKeyboardButton
import sqlite3
import json
import asyncio

# Конфигурация
BOT_TOKEN = "8168593612:AAHqKY0EdBmw4Xct0YoQj_qsCB67k95OWrU"
ADMIN_CHAT_ID = -1002703571150  # ID чата админов
SUPPORT_CHAT_ID = -1002703571150  # ID чата техподдержки
LOG_CHAT_ID = -4649797191  # ID чата логов
ADMINS = [1168675024]  # ID пользователей-админов

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# База данных
conn = sqlite3.connect('database.db', check_same_thread=False)
cursor = conn.cursor()

class OrderStates(StatesGroup):
    choosing_delivery = State()
    entering_full_name = State()
    entering_phone = State()
    entering_address = State()  # Новое состояние для адреса

class OrderHistoryStates(StatesGroup):
    viewing_orders = State()
    viewing_order_details = State()

# Функция для миграции базы данных
def migrate_database():
    try:
        # Проверяем существование таблиц
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [table[0] for table in cursor.fetchall()]

        # Создаем таблицу products, если ее нет (ваш оригинальный код)
        if 'products' not in tables:
            cursor.execute('''CREATE TABLE products (
                            id INTEGER PRIMARY KEY,
                            model TEXT NOT NULL,
                            name TEXT NOT NULL,
                            description TEXT,
                            condition TEXT NOT NULL,
                            price INTEGER NOT NULL CHECK(price > 0),
                            photos TEXT,
                            status TEXT DEFAULT 'approved',
                            seller_id INTEGER)''')
        else:
            # Проверяем существование столбцов products (ваш оригинальный код)
            cursor.execute("PRAGMA table_info(products)")
            columns = [column[1] for column in cursor.fetchall()]

            if 'model' not in columns:
                cursor.execute("ALTER TABLE products ADD COLUMN model TEXT NOT NULL DEFAULT 'Unknown'")
            if 'status' not in columns:
                cursor.execute("ALTER TABLE products ADD COLUMN status TEXT DEFAULT 'approved'")
            if 'seller_id' not in columns:
                cursor.execute("ALTER TABLE products ADD COLUMN seller_id INTEGER")

        # Создаем таблицу cart, если ее нет (ваш оригинальный код)
        if 'cart' not in tables:
            cursor.execute('''CREATE TABLE cart (
                            user_id INTEGER,
                            product_id INTEGER,
                            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

        if 'order_items' not in tables:
            cursor.execute('''CREATE TABLE order_items (
                                   order_id INTEGER,
                                   product_id INTEGER,
                                   quantity INTEGER DEFAULT 1,
                                   FOREIGN KEY(order_id) REFERENCES orders(id),
                                   FOREIGN KEY(product_id) REFERENCES products(id)
                               )''')

        # Модернизируем таблицу orders (ваш код + новые поля)
        if 'orders' not in tables:
            cursor.execute('''CREATE TABLE orders (
                                    id INTEGER PRIMARY KEY,
                                    user_id INTEGER,
                                    amount INTEGER,
                                    status TEXT,
                                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                    delivery_type TEXT,
                                    delivery_cost INTEGER,
                                    full_name TEXT,
                                    phone TEXT,
                                    address TEXT)''')  # Новый столбец
        else:
            cursor.execute("PRAGMA table_info(orders)")
            columns = [column[1] for column in cursor.fetchall()]

            if 'delivery_type' not in columns:           # NEW проверка
                cursor.execute("ALTER TABLE orders ADD COLUMN delivery_type TEXT")
            if 'delivery_cost' not in columns:           # NEW проверка
                cursor.execute("ALTER TABLE orders ADD COLUMN delivery_cost INTEGER DEFAULT 0")
            if 'full_name' not in columns:               # NEW проверка
                cursor.execute("ALTER TABLE orders ADD COLUMN full_name TEXT")
            if 'phone' not in columns:                   # NEW проверка
                cursor.execute("ALTER TABLE orders ADD COLUMN phone TEXT")
            if 'address' not in columns:  # Новая проверка
                cursor.execute("ALTER TABLE orders ADD COLUMN address TEXT")

        # Создаем таблицу admins, если ее нет (ваш оригинальный код)
        if 'admins' not in tables:
            cursor.execute('''CREATE TABLE admins (
                            user_id INTEGER PRIMARY KEY)''')

        conn.commit()
        logger.info("Database migration completed successfully")
    except Exception as e:
        logger.error(f"Database migration failed: {e}")
        conn.rollback()


# Выполняем миграцию
migrate_database()

# Добавляем админов в БД
for admin_id in ADMINS:
    try:
        cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (admin_id,))
    except sqlite3.IntegrityError:
        pass  # Игнорируем ошибки дубликатов
conn.commit()


# Состояния FSM
class CatalogStates(StatesGroup):
    browsing_models = State()
    browsing_products = State()
    viewing_product = State()


class SupportState(StatesGroup):
    waiting_for_message = State()


class AdminStates(StatesGroup):
    menu = State()
    waiting_product_id = State()
    waiting_new_value = State()
    waiting_field = State()
    waiting_order_id_for_status = State()  # NEW
    choosing_order_status = State()  # NEW


class AdminProductStates(StatesGroup): # Новые состояния для добавления товара админом
    waiting_for_model = State()
    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_condition = State()
    waiting_for_price = State()
    waiting_for_photos = State()
    waiting_for_photos_count = State()


class CartStates(StatesGroup):
    viewing_cart = State()


# Проверка админских прав
def is_admin(user_id: int) -> bool:
    try:
        cursor.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,))
        return cursor.fetchone() is not None
    except sqlite3.Error as e:
        logger.error(f"Admin check failed: {e}")
        return False


# Логирование действий
async def log_action(message: str):
    try:
        await bot.send_message(LOG_CHAT_ID, message)
        logger.info(f"Logged to chat: {message}")
    except Exception as e:
        logger.error(f"Failed to log to chat: {e}")


# Клавиатуры
def main_menu(user_id: int):
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="📱 Каталог"))
    builder.add(types.KeyboardButton(text="🛒 Корзина"))
    builder.add(types.KeyboardButton(text="📖 Мои заказы"))
    builder.add(types.KeyboardButton(text="🆘 Техподдержка"))

    if is_admin(user_id):
        builder.add(types.KeyboardButton(text="👑 Админ-панель"))

    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def cancel_button() -> InlineKeyboardBuilder:
    """Создает кнопку 'Отмена' для текущего сообщения."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="❌ Отмена",
        callback_data="cancel_action"
    ))
    return builder.as_markup()

def cancel_button_admin() -> InlineKeyboardBuilder:
    """Создает кнопку 'Отмена' для текущего сообщения."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="❌ Отмена",
        callback_data="cancel_action_admin"
    ))
    return builder.as_markup()


def delivery_kb():
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="🚗 Доставка (+2000 руб.)", callback_data="delivery_yes"))
    builder.add(types.InlineKeyboardButton(text="🏃 Самовывоз", callback_data="delivery_no"))
    builder.add(types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    builder.adjust(1)
    return builder.as_markup()

def back_kb():
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="🔙 Назад", callback_data="back"))
    return builder.as_markup()


def admin_menu_kb():
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="➕ Добавить товар", callback_data="admin_add_product"))
    builder.add(types.InlineKeyboardButton(text="✏️ Редактировать товар", callback_data="admin_edit_product"))
    builder.add(types.InlineKeyboardButton(text="❌ Удалить товар", callback_data="admin_delete_product"))
    builder.add(types.InlineKeyboardButton(text="📋 Список товаров", callback_data="admin_list_products"))
    builder.add(types.InlineKeyboardButton(text="📖 История заказов", callback_data="orders_categories"))
    builder.add(types.InlineKeyboardButton(text="🔄 Изменить статус заказа", callback_data="admin_change_order_status"))  # NEW
    builder.add(types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    builder.adjust(1)
    return builder.as_markup()

def order_status_kb():
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="В обработке", callback_data="status_processing"))
    builder.add(types.InlineKeyboardButton(text="Подтвержден", callback_data="status_confirmed"))
    builder.add(types.InlineKeyboardButton(text="В пути", callback_data="status_travel"))
    builder.add(types.InlineKeyboardButton(text="Готов к выдаче", callback_data="status_ready"))
    builder.add(types.InlineKeyboardButton(text="Доставлен", callback_data="status_delivered"))
    builder.add(types.InlineKeyboardButton(text="Выдан", callback_data="status_issued"))
    builder.add(types.InlineKeyboardButton(text="Отклонен", callback_data="status_rejected"))
    builder.add(types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    builder.adjust(2)
    return builder.as_markup()


def product_details_kb(product_id: object, in_cart: object = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if not in_cart:
        builder.add(types.InlineKeyboardButton(
            text="🛒 Добавить в корзину",
            callback_data=f"add_{product_id}"
        ))
    else:
        builder.add(types.InlineKeyboardButton(
            text="❌ Удалить из корзины",
            callback_data=f"remove_{product_id}"
        ))

    builder.add(types.InlineKeyboardButton(
        text="🔙 Назад",
        callback_data="back_to_products"
    ))
    builder.adjust(1)
    return builder.as_markup()


def cart_kb():
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="💳 Оформить заказ", callback_data="checkout"))
    builder.add(types.InlineKeyboardButton(text="🗑️ Очистить корзину", callback_data="clear_cart"))
    builder.add(types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action"))
    builder.adjust(1)
    return builder.as_markup()


# Хэндлеры
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(f"Добро пожаловать в магазин\nApple Cash!\nСвежие новинки, всегда в наличии и актуальные цены.",
                         reply_markup=main_menu(message.from_user.id))
    logger.info(f"User {message.from_user.id} started the bot")
    await log_action(f"🟢 Пользователь @{message.from_user.username} ({message.from_user.id}) запустил бота")


@dp.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext):
    if message.chat.type != "private":
        await message.reply("⚠️ Админ-панель доступна только в личных сообщениях с ботом")
        return

    if not is_admin(message.from_user.id):
        await message.reply("🚫 У вас нет прав администратора")
        return

    await state.set_state(AdminStates.menu)
    await message.answer("👑 Админ-панель:", reply_markup=admin_menu_kb())
    await log_action(f"👑 Админ @{message.from_user.username} ({message.from_user.id}) открыл админ-панель")

@dp.callback_query(AdminStates.menu, F.data == "admin_change_order_status")
async def admin_change_order_status_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_order_id_for_status)
    await callback.message.delete()
    await callback.message.answer("Введите ID заказа, статус которого хотите изменить:", reply_markup=cancel_button_admin())
    await callback.answer()


@dp.message(AdminStates.waiting_order_id_for_status)
async def process_order_id_for_status(message: Message, state: FSMContext):
    try:
        order_id = int(message.text)
        cursor.execute("SELECT user_id, status FROM orders WHERE id=?", (order_id,))
        order_info = cursor.fetchone()

        if not order_info:
            await message.answer("❌ Заказ с таким ID не найден.")
            return

        user_id, current_status = order_info
        await state.update_data(current_order_id=order_id, order_user_id=user_id)
        await state.set_state(AdminStates.choosing_order_status)
        await message.answer(f"Текущий статус заказа #{order_id}: *{current_status}*\nВыберите новый статус:",
                             reply_markup=order_status_kb(), parse_mode="Markdown")

    except ValueError:
        await message.answer("❌ Неверный формат ID. Введите число.")
    except sqlite3.Error as e:
        logger.error(f"Error processing order ID for status change: {e}")
        await message.answer("⚠️ Произошла ошибка при обработке запроса.")


@dp.callback_query(F.data == "cancel_action")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    try:
        # 1. Удаляем сообщение с кнопкой
        await callback.message.delete()

        # 2. Очищаем состояние FSM (если используется)
        await state.clear()

        # 3. Возвращаем в главное меню
        await callback.message.answer(
            "Вы отменили действие.",
            reply_markup=main_menu(callback.from_user.id)  # Ваша функция для главного меню
        )
    except Exception as e:
        await callback.answer("⚠️ Ошибка отмены", show_alert=True)


@dp.callback_query(F.data == "cancel_action_admin")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    try:
        # 1. Удаляем сообщение с кнопкой
        await callback.message.delete()

        # 2. Очищаем состояние FSM (если используется)
        await state.clear()

        # 3. Возвращаем в админ меню
        await state.set_state(AdminStates.menu)
        await callback.message.answer("👑 Админ-панель:", reply_markup=admin_menu_kb())
        await log_action(f"👑 Админ @{callback.message.from_user.username} ({callback.message.from_user.id}) открыл админ-панель")
    except Exception as e:
        await callback.answer("⚠️ Ошибка отмены", show_alert=True)


@dp.callback_query(AdminStates.choosing_order_status, F.data.startswith("status_"))
async def update_order_status(callback: CallbackQuery, state: FSMContext):
    try:
        new_status_key = callback.data.split('_')[1]
        status_map = {
            "processing": "В обработке",
            "confirmed": "Подтвержден",
            "travel": "В пути",
            "ready": "Готов к выдаче",
            "delivered": "Доставлен",
            "issued": "Выдан",
            "rejected": "Отклонен"
        }
        new_status_text = status_map.get(new_status_key, "Неизвестный статус")

        data = await state.get_data()
        order_id = data["current_order_id"]
        order_user_id = data["order_user_id"]

        cursor.execute("UPDATE orders SET status=? WHERE id=?", (new_status_text, order_id))
        conn.commit()

        await callback.message.edit_text(f"✅ Статус заказа #{order_id} успешно изменен на: *{new_status_text}*",
                                         parse_mode="Markdown")
        await callback.answer()
        await log_action(f"🔄 Админ изменил статус заказа ID {order_id} на '{new_status_text}'")

        # Уведомление пользователя
        try:
            await bot.send_message(order_user_id,
                                   f"🔔 Статус вашего заказа #{order_id} изменен на: *{new_status_text}*",
                                   parse_mode="Markdown")
            logger.info(f"User {order_user_id} notified about order {order_id} status change to {new_status_text}")
        except Exception as e:
            logger.error(f"Failed to notify user {order_user_id} about order status change: {e}")

    except sqlite3.Error as e:
        logger.error(f"Error updating order status: {e}")
        await callback.message.answer("⚠️ Произошла ошибка при обновлении статуса заказа.")
    finally:
        await state.clear()

@dp.message(F.text == "👑 Админ-панель")
async def admin_panel_button(message: Message, state: FSMContext):
    if message.chat.type != "private":
        return

    if not is_admin(message.from_user.id):
        await message.reply("🚫 У вас нет прав администратора")
        return

    await state.set_state(AdminStates.menu)
    await message.answer("👑 Админ-панель:", reply_markup=admin_menu_kb())
    await log_action(f"👑 Админ @{message.from_user.username} ({message.from_user.id}) открыл админ-панель")


@dp.message(F.text == "📱 Каталог")
async def show_catalog(message: Message, state: FSMContext):
    await state.set_state(CatalogStates.browsing_models)
    await show_models(message)


async def show_models(message: Message):
    try:
        cursor.execute("SELECT DISTINCT model FROM products WHERE status='approved'")
        models = cursor.fetchall()

        if not models:
            await message.answer("Каталог пуст", reply_markup=back_kb())
            return

        builder = InlineKeyboardBuilder()
        for model in models:
            builder.add(types.InlineKeyboardButton(
                text=model[0],
                callback_data=f"model_{model[0]}"
            ))
        builder.adjust(2)
        await message.answer("Выберите модель:", reply_markup=builder.as_markup())
    except sqlite3.Error as e:
        logger.error(f"Error showing models: {e}")
        await message.answer("⚠️ Произошла ошибка при загрузке каталога. Попробуйте позже.")


@dp.callback_query(F.data.startswith("model_"))
async def show_products_for_model(callback: CallbackQuery, state: FSMContext):
    try:
        model = callback.data.split('_', 1)[1]
        await state.update_data(current_model=model)
        await state.set_state(CatalogStates.browsing_products)

        cursor.execute("""
            SELECT id, name, price, condition 
            FROM products 
            WHERE model=? AND status='approved'
        """, (model,))
        products = cursor.fetchall()

        if not products:
            await callback.message.edit_text("В этой категории пока нет товаров")
            return

        builder = InlineKeyboardBuilder()
        for product in products:
            builder.add(types.InlineKeyboardButton(
                text=f"{product[1]} - {product[2]} руб. ({product[3]})",
                callback_data=f"product_{product[0]}"
            ))
        builder.adjust(1)
        await callback.message.edit_text(
            f"Товары модели {model}:",
            reply_markup=builder.as_markup()
        )
    except sqlite3.Error as e:
        logger.error(f"Error showing products for model: {e}")
        await callback.message.answer("⚠️ Произошла ошибка. Попробуйте позже.")


@dp.callback_query(F.data.startswith("product_"))
async def show_product_details(callback: CallbackQuery, state: FSMContext):
    try:
        product_id = int(callback.data.split('_')[1])
        cursor.execute("SELECT * FROM products WHERE id=?", (product_id,))
        product = cursor.fetchone()

        if not product:
            await callback.answer("Товар не найден")
            return

        # Проверяем, есть ли товар уже в корзине
        cursor.execute("SELECT 1 FROM cart WHERE user_id=? AND product_id=?",
                       (callback.from_user.id, product_id))
        in_cart = cursor.fetchone() is not None

        photos = product[6].split(',')
        caption = (
            f"📱 Модель: {product[1]}\n"
            f"🔖 Название: {product[2]}\n"
            f"ℹ️ Описание: {product[3]}\n"
            f"🔧 Состояние: {product[4]}\n"
            f"💰 Цена: {product[5]} руб."
        )

        # Отправляем первое фото с описанием
        await bot.send_photo(
            chat_id=callback.message.chat.id,
            photo=photos[0],
            caption=caption,
            reply_markup=product_details_kb(product_id, in_cart)
        )
        await state.set_state(CatalogStates.viewing_product)
        await state.update_data(current_product=product_id, photo_index=0, photos=photos)
    except Exception as e:
        logger.error(f"Error showing product details: {e}")
        await callback.message.answer("⚠️ Произошла ошибка при загрузке товара.")


@dp.callback_query(F.data.startswith("add_"))
async def add_to_cart(callback: CallbackQuery, state: FSMContext):
    try:
        product_id = int(callback.data.split('_')[1])
        user_id = callback.from_user.id

        # Проверяем, есть ли товар уже в корзине
        cursor.execute("SELECT 1 FROM cart WHERE user_id=? AND product_id=?",
                       (user_id, product_id))
        if cursor.fetchone():
            await callback.answer("Товар уже в корзине!")
            return

        # Добавляем товар в корзину
        cursor.execute("INSERT INTO cart (user_id, product_id) VALUES (?, ?)",
                       (user_id, product_id))
        conn.commit()

        await callback.answer("✅ Товар добавлен в корзину!")

        # Обновляем кнопку
        await callback.message.edit_reply_markup(reply_markup=product_details_kb(product_id, True))

        await log_action(
            f"🛒 Пользователь @{callback.from_user.username} ({user_id}) добавил товар {product_id} в корзину")
    except Exception as e:
        logger.error(f"Error adding to cart: {e}")
        await callback.answer("⚠️ Не удалось добавить товар в корзину")


@dp.callback_query(F.data.startswith("remove_"))
async def remove_from_cart(callback: CallbackQuery):
    try:
        product_id = int(callback.data.split('_')[1])
        user_id = callback.from_user.id

        # Удаляем товар из корзины
        cursor.execute("DELETE FROM cart WHERE user_id=? AND product_id=?",
                       (user_id, product_id))
        conn.commit()

        await callback.answer("✅ Товар удалён из корзины!")

        # Обновляем кнопку
        await callback.message.edit_reply_markup(reply_markup=product_details_kb(product_id, False))

        await log_action(
            f"🗑️ Пользователь @{callback.from_user.username} ({user_id}) удалил товар {product_id} из корзины")
    except Exception as e:
        logger.error(f"Error removing from cart: {e}")
        await callback.answer("⚠️ Не удалось удалить товар из корзины")


@dp.message(F.text == "🛒 Корзина")
async def show_cart(message: Message, state: FSMContext):
    try:
        user_id = message.from_user.id

        # Получаем товары в корзине
        cursor.execute("""
            SELECT p.id, p.name, p.price, p.condition 
            FROM cart c
            JOIN products p ON c.product_id = p.id
            WHERE c.user_id = ?
        """, (user_id,))
        cart_items = cursor.fetchall()

        if not cart_items:
            await message.answer("🛒 Ваша корзина пуста")
            return

        total = 0
        response = "🛒 Ваша корзина:\n\n"
        for item in cart_items:
            response += f"📱 {item[1]}\n💵 {item[2]} руб.\n🔧 {item[3]}\n\n"
            total += item[2]

        response += f"💳 Итого: {total} руб."

        await state.set_state(CartStates.viewing_cart)
        await message.answer(response, reply_markup=cart_kb())
    except Exception as e:
        logger.error(f"Error showing cart: {e}")
        await message.answer("⚠️ Произошла ошибка при загрузке корзины")


@dp.callback_query(F.data == "clear_cart")
async def clear_cart(callback: CallbackQuery):
    try:
        user_id = callback.from_user.id
        cursor.execute("DELETE FROM cart WHERE user_id=?", (user_id,))
        conn.commit()

        await callback.message.edit_text("🗑️ Корзина очищена")
        await callback.answer()
        await log_action(f"🧹 Пользователь @{callback.from_user.username} ({user_id}) очистил корзину")
    except Exception as e:
        logger.error(f"Error clearing cart: {e}")
        await callback.answer("⚠️ Не удалось очистить корзину")


@dp.callback_query(F.data == "checkout")
async def process_checkout(callback: CallbackQuery, state: FSMContext):
    try:
        user_id = callback.from_user.id

        # Получаем товары в корзине
        cursor.execute("""
            SELECT p.id, p.name, p.price
            FROM cart c
            JOIN products p ON c.product_id = p.id
            WHERE c.user_id = ?
        """, (user_id,))
        cart_items = cursor.fetchall()

        if not cart_items:
            await callback.answer("Ваша корзина пуста!")
            return

        # Рассчитываем общую сумму
        total = sum(item[2] for item in cart_items)

        # Сохраняем информацию о заказе в состоянии
        await state.update_data(
            cart_items=cart_items,
            total=total,
            user_id=user_id
        )

        # Переходим к выбору способа доставки
        await state.set_state(OrderStates.choosing_delivery)
        await callback.message.answer(
            "Выберите способ получения заказа:\n\n"
            "🚗 Доставка - стоимость 2000 руб. будет добавлена к сумме заказа\n"
            "🏃 Самовывоз - бесплатно, по адресу: г. Москва, ул. Примерная, д. 123",
            reply_markup=delivery_kb()
        )
        await callback.answer()

    except Exception as e:
        logger.error(f"Error processing checkout: {e}")
        await callback.answer("⚠️ Произошла ошибка при оформлении заказа")


@dp.callback_query(OrderStates.choosing_delivery, F.data.startswith("delivery_"))
async def process_delivery_choice(callback: CallbackQuery, state: FSMContext):
    try:
        delivery_type = callback.data.split('_')[1]
        data = await state.get_data()
        total = data['total']

        if delivery_type == "yes":
            delivery_cost = 2000
            total += delivery_cost
            await state.update_data(delivery_type="delivery", delivery_cost=delivery_cost, total=total)
            await callback.message.answer(
                f"Вы выбрали доставку. Стоимость доставки: 2000 руб.\n"
                f"Общая сумма заказа: {total} руб.\n\n"
                "Теперь введите ваше ФИО:"
            )
        else:
            await state.update_data(delivery_type="pickup", delivery_cost=0)
            await callback.message.answer(
                f"Вы выбрали самовывоз. Общая сумма заказа: {total} руб.\n\n"
                "Теперь введите ваше ФИО:"
            )

        await state.set_state(OrderStates.entering_full_name)
        await callback.answer()

    except Exception as e:
        logger.error(f"Error processing delivery choice: {e}")
        await callback.message.answer("⚠️ Произошла ошибка. Попробуйте снова.")
        await state.clear()


@dp.message(OrderStates.entering_full_name)
async def process_full_name(message: Message, state: FSMContext):
    if len(message.text) < 5:
        await message.answer("ФИО должно содержать не менее 5 символов. Попробуйте еще раз.")
        return

    await state.update_data(full_name=message.text)
    await state.set_state(OrderStates.entering_phone)
    await message.answer("Теперь введите ваш контактный телефон (в формате +79991234567):")


@dp.message(OrderStates.entering_phone)
async def process_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    if not phone.startswith('+') or not phone[1:].isdigit() or len(phone) < 11:
        await message.answer("Неверный формат телефона. Введите телефон в формате +79991234567")
        return

    data = await state.get_data()
    await state.update_data(phone=phone)

    # Если выбрана доставка - запрашиваем адрес
    if data.get('delivery_type') == "delivery":
        await message.answer("Введите адрес доставки (улица, дом, квартира):")
        await state.set_state(OrderStates.entering_address)
    else:
        # Для самовывоза сразу завершаем заказ
        await complete_order(message, state)


@dp.message(OrderStates.entering_address)
async def process_address(message: Message, state: FSMContext):
    if len(message.text) < 10:
        await message.answer("Адрес слишком короткий (минимум 10 символов). Введите полный адрес")
        return

    await state.update_data(address=message.text)
    await complete_order(message, state)

@dp.callback_query(F.data == "pay_order")
async def pay_order(callback: CallbackQuery):
    # Вызываем функцию для отправки фото
    await send_local_photo(callback.message)

    # Дополнительная логика, если необходимо
    await callback.answer("Вы выбрали оплату заказа.")

async def send_local_photo(message: Message):
    # Путь к файлу на сервере/компьютере
    photo = FSInputFile("photo_5235676717530608878_y.jpg")

    await message.answer_photo(photo, caption="Для подтверждения заказа, просим вас оплатить заказ по ссылке или же по QR-коду. В коментариях платежа просим указать номер заказать. Оплачивать заказ строго ОДНИМ платежем!\n https://tbank.ru/cf/7yRBYgWlMIR")


@dp.message(F.text == "🆘 Техподдержка")
async def ask_support(message: Message, state: FSMContext):
    if message.chat.type != "private":
        await message.reply("✉️ Пожалуйста, напишите в личные сообщения бота для техподдержки")
        return

    await state.set_state(SupportState.waiting_for_message)
    await message.answer("Опишите вашу проблему или вопрос. Мы ответим как можно скорее:\n+71234567890\nexample@mail.ru\nЕсли вы хотите предложить товар, пишите нам на почту или связывайтесь по номеру",
                         reply_markup=cancel_button())
    await log_action(f"🆘 Пользователь @{message.from_user.username} ({message.from_user.id}) запросил техподдержку")


@dp.message(SupportState.waiting_for_message)
async def send_to_support(message: Message, state: FSMContext):
    support_text = (
        f"🆘 Новый запрос в поддержку\n"
        f"👤 Пользователь: @{message.from_user.username or message.from_user.full_name}\n"
        f"🆔 ID: {message.from_user.id}\n\n"
        f"✉️ Сообщение:\n{message.text}"
    )

    try:
        await bot.send_message(SUPPORT_CHAT_ID, support_text)
        await message.answer("✅ Ваше сообщение отправлено в поддержку. Ожидайте ответа!",
                             reply_markup=main_menu(message.from_user.id))
        await log_action(f"📩 Запрос в поддержку от @{message.from_user.username} отправлен в чат техподдержки")
    except Exception as e:
        logger.error(f"Failed to send support message: {e}")
        await message.answer("⚠️ Не удалось отправить сообщение в поддержку. Попробуйте позже.")

    await state.clear()


# Админские команды
@dp.callback_query(AdminStates.menu, F.data == "admin_add_product")
async def admin_add_product_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminProductStates.waiting_for_model)
    await callback.message.delete()
    await callback.message.answer("Введите модель iPhone для добавления:", reply_markup=cancel_button_admin())
    await callback.answer()



@dp.callback_query(AdminStates.menu, F.data == "admin_list_products")
async def admin_list_products(callback: CallbackQuery):
    try:
        cursor.execute("SELECT id, model, name, price FROM products")
        products = cursor.fetchall()

        if not products:
            await callback.message.delete()
            await callback.message.answer("Нет товаров в базе данных")
            await callback.answer()
            return

        response = "📦 Список товаров:\n\n"
        await callback.message.delete()
        for product in products:
            response += f"🆔 ID: {product[0]}\n📱 Модель: {product[1]}\n🔖 Название: {product[2]}\n💰 Цена: {product[3]} руб.\n\n"

        await callback.message.answer(response)
    except sqlite3.Error as e:
        logger.error(f"Error listing products: {e}")
        await callback.message.answer("⚠️ Произошла ошибка при загрузке списка товаров.")
    await callback.answer()


@dp.callback_query(AdminStates.menu, F.data == "admin_edit_product")
async def admin_edit_product(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_product_id)
    await callback.message.delete()
    await callback.message.answer("Введите ID товара для редактирования:", reply_markup=cancel_button_admin())
    await callback.answer()


@dp.callback_query(AdminStates.menu, F.data == "admin_delete_product")
async def admin_delete_product(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_product_id)
    await state.update_data(action="delete")
    await callback.message.delete()
    await callback.message.answer("Введите ID товара для удаления:", reply_markup=cancel_button_admin())
    await callback.answer()


@dp.message(AdminStates.waiting_product_id)
async def process_product_id(message: Message, state: FSMContext):
    try:
        product_id = int(message.text)
        cursor.execute("SELECT * FROM products WHERE id=?", (product_id,))
        product = cursor.fetchone()

        if not product:
            await message.answer("❌ Товар с таким ID не найден")
            return

        data = await state.get_data()
        action = data.get("action")

        if action == "delete":
            cursor.execute("DELETE FROM products WHERE id=?", (product_id,))
            conn.commit()
            await message.answer(f"✅ Товар ID {product_id} успешно удален")
            await state.clear()
            await log_action(f"❌ Админ удалил товар ID {product_id}")
            return

        await state.update_data(product_id=product_id)
        await state.set_state(AdminStates.waiting_field)

        builder = InlineKeyboardBuilder()
        fields = ["model", "name", "description", "condition", "price"]
        for field in fields:
            builder.add(types.InlineKeyboardButton(
                text=field.capitalize(),
                callback_data=f"edit_field_{field}"
            ))
        builder.adjust(2)

        await message.answer("Выберите поле для редактирования:", reply_markup=builder.as_markup())

    except ValueError:
        await message.answer("❌ Неверный формат ID. Введите число.")
    except sqlite3.Error as e:
        logger.error(f"Error processing product ID: {e}")
        await message.answer("⚠️ Произошла ошибка при обработке запроса.")


@dp.callback_query(AdminStates.waiting_field, F.data.startswith("edit_field_"))
async def select_field_to_edit(callback: CallbackQuery, state: FSMContext):
    field = callback.data.split("_")[2]
    await state.update_data(field=field)
    await state.set_state(AdminStates.waiting_new_value)
    await callback.message.answer(f"Введите новое значение для поля '{field}':")
    await callback.answer()


@dp.message(AdminStates.waiting_new_value)
async def update_product_field(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        product_id = data["product_id"]
        field = data["field"]
        new_value = message.text

        # Валидация цены
        if field == "price":
            try:
                price = int(new_value)
                if price <= 0:
                    await message.answer("❌ Цена должна быть положительным числом")
                    return
                new_value = price
            except ValueError:
                await message.answer("❌ Неверный формат цены. Введите число.")
                return

        # Обновление в БД
        cursor.execute(f"UPDATE products SET {field}=? WHERE id=?", (new_value, product_id))
        conn.commit()

        await message.answer(f"✅ Поле '{field}' товара ID {product_id} успешно обновлено")
        await log_action(f"✏️ Админ обновил товар ID {product_id}: {field} → {new_value}")
    except sqlite3.Error as e:
        logger.error(f"Error updating product field: {e}")
        await message.answer("⚠️ Произошла ошибка при обновлении товара.")
    finally:
        await state.clear()


# Обработка возвратов
@dp.callback_query(F.data == "back")
async def handle_back(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()

    if current_state == CatalogStates.browsing_products:
        await state.set_state(CatalogStates.browsing_models)
        await show_models(callback.message)
        await callback.message.delete()

    elif current_state == CatalogStates.viewing_product:
        data = await state.get_data()
        model = data.get('current_model', '')
        await state.set_state(CatalogStates.browsing_products)

        try:
            cursor.execute("""
                SELECT id, name, price, condition 
                FROM products 
                WHERE model=? AND status='approved'
            """, (model,))
            products = cursor.fetchall()

            if products:
                builder = InlineKeyboardBuilder()
                for product in products:
                    builder.add(types.InlineKeyboardButton(
                        text=f"{product[1]} - {product[2]} руб. ({product[3]})",
                        callback_data=f"product_{product[0]}"
                    ))
                builder.adjust(1)

                await callback.message.answer(
                    f"Товары модели {model}:",
                    reply_markup=builder.as_markup()
                )
            else:
                await callback.message.answer(f"В модели {model} больше нет товаров")
        except sqlite3.Error as e:
            logger.error(f"Error returning to products: {e}")
            await callback.message.answer("⚠️ Произошла ошибка при загрузке товаров.")

        await callback.message.delete()

    elif current_state == SupportState.waiting_for_message:
        await state.clear()
        await callback.message.answer("Главное меню:", reply_markup=main_menu(callback.from_user.id))

    elif current_state == AdminStates.menu:
        await state.clear()
        await callback.message.answer("Главное меню:", reply_markup=main_menu(callback.from_user.id))

    elif current_state == CartStates.viewing_cart:
        await state.clear()
        await callback.message.answer("Главное меню:", reply_markup=main_menu(callback.from_user.id))

    elif isinstance(current_state, AdminStates): # Если текущее состояние - любое из админских
        await state.set_state(AdminStates.menu)
        await callback.message.answer("👑 Админ-панель:", reply_markup=admin_menu_kb())

    elif isinstance(current_state, AdminProductStates): # Если текущее состояние - любое из добавления товара админом
        await state.set_state(AdminStates.menu)
        await callback.message.answer("👑 Админ-панель:", reply_markup=admin_menu_kb())

    await callback.answer()


@dp.callback_query(F.data == "back_to_main")
async def back_to_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Главное меню:", reply_markup=main_menu(callback.from_user.id))
    await callback.answer()


@dp.callback_query(F.data == "back_to_products")
async def back_to_products_list(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    model = data.get('current_model', '')
    await state.set_state(CatalogStates.browsing_products)

    try:
        cursor.execute("""
            SELECT id, name, price, condition 
            FROM products 
            WHERE model=? AND status='approved'
        """, (model,))
        products = cursor.fetchall()

        if products:
            builder = InlineKeyboardBuilder()
            for product in products:
                builder.add(types.InlineKeyboardButton(
                    text=f"{product[1]} - {product[2]} руб. ({product[3]})",
                    callback_data=f"product_{product[0]}"
                ))
            builder.adjust(1)

            await callback.message.answer(
                f"Товары модели {model}:",
                reply_markup=builder.as_markup()
            )
        else:
            await callback.message.answer(f"В модели {model} больше нет товаров")
    except sqlite3.Error as e:
        logger.error(f"Error returning to products: {e}")
        await callback.message.answer("⚠️ Произошла ошибка при загрузке товаров.")

    await callback.message.delete()
    await callback.answer()


# Обработка добавления товара (ТОЛЬКО для админов)
@dp.message(AdminProductStates.waiting_for_model)
async def process_admin_model(message: Message, state: FSMContext):
    if len(message.text) > 50:
        await message.answer("Название модели слишком длинное. Максимум 50 символов.")
        return
    await state.update_data(model=message.text)
    await state.set_state(AdminProductStates.waiting_for_name)
    await message.answer("Введите краткое название товара (например, iPhone 12 Pro 256GB Blue):")


@dp.message(AdminProductStates.waiting_for_name)
async def process_admin_name(message: Message, state: FSMContext):
    if len(message.text) > 100:
        await message.answer("Название слишком длинное. Максимум 100 символов.")
        return
    await state.update_data(name=message.text)
    await state.set_state(AdminProductStates.waiting_for_description)
    await message.answer("Введите описание товара:")


@dp.message(AdminProductStates.waiting_for_description)
async def process_admin_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    await state.set_state(AdminProductStates.waiting_for_condition)
    await message.answer("Опишите состояние телефона (новый, б/у, повреждения):")


@dp.message(AdminProductStates.waiting_for_condition)
async def process_admin_condition(message: Message, state: FSMContext):
    await state.update_data(condition=message.text)
    await state.set_state(AdminProductStates.waiting_for_price)
    await message.answer("Укажите цену в рублях (только цифры):")


@dp.message(AdminProductStates.waiting_for_price)
async def process_admin_price(message: Message, state: FSMContext):
    try:
        price = int(message.text)
        if price <= 0:
            await message.answer("Цена должна быть положительным числом. Попробуйте еще раз.")
            return
        await state.update_data(price=price)
        await state.set_state(AdminProductStates.waiting_for_photos_count)
        await message.answer("Сколько фото вы хотите отправить? (1-3)")
    except ValueError:
        await message.answer("Пожалуйста, введите число:")


@dp.message(AdminProductStates.waiting_for_photos_count)
async def process_admin_photos_count(message: Message, state: FSMContext):
    try:
        count = int(message.text)
        if 1 <= count <= 3:
            await state.update_data(photos_count=count, photos=[])
            await state.set_state(AdminProductStates.waiting_for_photos)
            await message.answer(f"Отправьте {count} фото товара:")
        else:
            await message.answer("Отправьте число от 1 до 3:")
    except ValueError:
        await message.answer("Пожалуйста, введите число:")


@dp.message(AdminProductStates.waiting_for_photos, F.photo)
async def process_admin_photos(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        photos = data.get('photos', [])
        photos.append(message.photo[-1].file_id)

        if len(photos) >= data['photos_count']:
            await state.update_data(photos=','.join(photos))
            data = await state.get_data()

            # Для админа статус всегда 'approved'
            status = "approved"

            # Сохраняем товар в БД
            cursor.execute("""
                INSERT INTO products 
                (model, name, description, condition, price, photos, status, seller_id) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data['model'], data['name'], data['description'],
                data['condition'], data['price'], data['photos'],
                status, message.from_user.id
            ))
            conn.commit()

            product_id = cursor.lastrowid

            await message.answer("✅ Товар успешно добавлен в каталог!",
                                 reply_markup=main_menu(message.from_user.id))
            await log_action(f"🆕 Админ добавил товар ID {product_id} в каталог")

            await state.clear()
        else:
            await state.update_data(photos=photos)
            remaining = data['photos_count'] - len(photos)
            await message.answer(f"✅ Фото принято. Осталось отправить: {remaining}")
    except Exception as e:
        logger.error(f"Error processing photos: {e}")
        await message.answer("⚠️ Произошла ошибка при обработке товара. Попробуйте снова.")
        await state.clear()

async def complete_order(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        user_id = data['user_id']
        cart_items = data['cart_items']
        total = data['total']
        delivery_type = data.get('delivery_type', 'pickup')
        delivery_cost = data.get('delivery_cost', 0)
        full_name = data['full_name']
        phone = data['phone']
        address = data.get('address', 'Самовывоз: г. Москва, ул. Примерная, 123')

        # Создаем заказ
        cursor.execute("""
            INSERT INTO orders 
            (user_id, amount, status, delivery_type, delivery_cost, full_name, phone, address) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, total, 'В обработке',
            delivery_type, delivery_cost,
            full_name, phone, address
        ))
        order_id = cursor.lastrowid

        # Добавляем товары в order_items
        for item in cart_items:
            cursor.execute("""
                INSERT INTO order_items (order_id, product_id)
                VALUES (?, ?)
            """, (order_id, item[0]))

        # Очищаем корзину
        cursor.execute("DELETE FROM cart WHERE user_id=?", (user_id,))
        conn.commit()

        # Формируем сообщение для покупателя
        order_details = (
            f"✅ Заказ #{order_id} оформлен!\n"
            f"👤 ФИО: {full_name}\n"
            f"📞 Телефон: {phone}\n"
            f"🚚 Способ: {'Доставка' if delivery_type == 'delivery' else 'Самовывоз'}\n"
        )

        if delivery_type == "delivery":
            order_details += f"🏠 Адрес: {address}\n"

        order_details += f"💳 Сумма: {total} руб.\n\nСостав заказа:\n"
        for item in cart_items:
            order_details += f"• {item[1]} - {item[2]} руб.\n"

        markup = InlineKeyboardBuilder()
        markup.add(types.InlineKeyboardButton(text="📦 Оплатить заказ", callback_data="pay_order"))

        await message.answer(order_details, reply_markup=markup.as_markup())

        # Отправка админам (добавляем адрес)
        admin_order_info = (
            f"💰 *НОВЫЙ ЗАКАЗ!* #`{order_id}`\n"
            f"👤 Пользователь: @{message.from_user.username or message.from_user.full_name}\n"
            f"📞 Телефон: `{phone}`\n"
            f"🏠 Адрес: `{address}`\n"
            f"💳 Сумма: *{total} руб.*\n"
            f"📦 *Состав заказа:*\n"
        )
        for item in cart_items:
            admin_order_info += f"• `{item[1]}` (ID: `{item[0]}`) - `{item[2]}` руб.\n"

        await bot.send_message(ADMIN_CHAT_ID, admin_order_info, parse_mode="Markdown")


        await state.clear()

    except Exception as e:
        logger.error(f"Ошибка завершения заказа: {e}")
        await message.answer("⚠️ Ошибка при оформлении заказа")
        await state.clear()


# Добавим функцию для создания клавиатуры пагинации
def orders_pagination_kb(current_page: int, total_pages: int, order_type: str, is_admin: bool = False):
    builder = InlineKeyboardBuilder()

    # Кнопки навигации
    if current_page > 1:
        builder.add(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"orders_{order_type}_{current_page - 1}"))

    if current_page < total_pages:
        builder.add(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"orders_{order_type}_{current_page + 1}"))

    # Кнопки переключения между активными и архивными заказами
    builder.add(InlineKeyboardButton(
        text="📌 Активные" if order_type == "archived" else "🗂 Архивные",
        callback_data=f"switch_orders_{'active' if order_type == 'archived' else 'archived'}_1"
    ))

    # Кнопка возврата
    if is_admin:
        builder.add(InlineKeyboardButton(text="🔙 В админ-панель", callback_data="back_to_admin"))
    else:
        builder.add(InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main"))

    builder.adjust(2)
    return builder.as_markup()


# Обработчик кнопки "Мои заказы"
@dp.message(F.text == "📖 Мои заказы")
async def show_user_orders(message: Message, state: FSMContext):
    await state.set_state(OrderHistoryStates.viewing_orders)
    await show_orders_page(message, message.from_user.id, "active", 1, is_admin=False)


# Обработчик кнопки "История заказов" в админ-панели
@dp.callback_query(AdminStates.menu, F.data == "orders_categories")
async def admin_orders_history(callback: CallbackQuery, state: FSMContext):
    await state.set_state(OrderHistoryStates.viewing_orders)
    await callback.message.delete()
    await show_orders_page(callback.message, None, "active", 1, is_admin=True)


# Функция для отображения страницы с заказами
async def show_orders_page(message: Message, user_id: int | None, order_type: str, page: int, is_admin: bool):
    try:
        offset = (page - 1) * 10
        status_filter = ["Отклонен", "Выдан"] if order_type == "archived" else ["В обработке", "Подтвержден", "В пути",
                                                                                "Готов к выдаче", "Доставлен"]

        query = """
            SELECT id, amount, status, created_at 
            FROM orders 
            WHERE status IN ({}) 
            {}
            ORDER BY created_at DESC 
            LIMIT 10 OFFSET ?
        """.format(
            ",".join(["?"] * len(status_filter)),
            "AND user_id=?" if user_id else ""
        )

        params = status_filter.copy()
        if user_id:
            params.append(user_id)
        params.append(offset)

        cursor.execute(query, params)
        orders = cursor.fetchall()

        # Получаем общее количество заказов для пагинации
        count_query = """
            SELECT COUNT(*) 
            FROM orders 
            WHERE status IN ({}) 
            {}
        """.format(
            ",".join(["?"] * len(status_filter)),
            "AND user_id=?" if user_id else ""
        )

        count_params = status_filter.copy()
        if user_id:
            count_params.append(user_id)

        cursor.execute(count_query, count_params)
        total_orders = cursor.fetchone()[0]
        total_pages = max(1, (total_orders + 9) // 10)

        if not orders:
            await message.answer(f"Нет {'архивных' if order_type == 'archived' else 'активных'} заказов.")
            return

        response = f"📖 {'Все заказы' if is_admin else 'Мои заказы'} ({'архивные' if order_type == 'archived' else 'активные'})\n\n"
        response += f"Страница {page} из {total_pages}\n\n"

        for order in orders:
            order_id, amount, status, created_at = order
            response += (
                f"🆔 Заказ #{order_id}\n"
                f"💳 Сумма: {amount} руб.\n"
                f"📦 Статус: {status}\n"
                f"📅 Дата: {created_at}\n"
                f"🔍 /order_{order_id}\n\n"
            )

        await message.answer(
            response,
            reply_markup=orders_pagination_kb(page, total_pages, order_type, is_admin)
        )

    except Exception as e:
        logger.error(f"Error showing orders page: {e}")
        await message.answer("⚠️ Произошла ошибка при загрузке заказов.")


# Обработчик переключения между страницами и типами заказов
@dp.callback_query(OrderHistoryStates.viewing_orders, F.data.startswith("orders_"))
async def handle_orders_pagination(callback: CallbackQuery, state: FSMContext):
    _, order_type, page_str = callback.data.split("_")
    page = int(page_str)

    data = await state.get_data()
    user_id = data.get("user_id", callback.from_user.id)
    is_admin_mode = "back_to_admin" in (await state.get_data()).get("callback_data", "")

    await show_orders_page(callback.message, None if is_admin_mode else user_id, order_type, page,
                           is_admin=is_admin_mode)
    await callback.answer()


@dp.callback_query(OrderHistoryStates.viewing_orders, F.data.startswith("switch_orders_"))
async def switch_orders_type(callback: CallbackQuery, state: FSMContext):
    _, _, order_type, page_str = callback.data.split("_")
    page = int(page_str)

    data = await state.get_data()
    user_id = data.get("user_id", callback.from_user.id)
    is_admin_mode = "back_to_admin" in (await state.get_data()).get("callback_data", "")

    await show_orders_page(callback.message, None if is_admin_mode else user_id, order_type, page,
                           is_admin=is_admin_mode)
    await callback.answer()


# Обработчик просмотра деталей заказа
@dp.message(OrderHistoryStates.viewing_orders, F.text.startswith("/order_"))
async def show_order_details(message: Message, state: FSMContext):
    try:
        order_id = int(message.text.split("_")[1])

        cursor.execute("""
            SELECT id, amount, status, created_at, delivery_type, delivery_cost, full_name, phone, address 
            FROM orders 
            WHERE id=?
        """, (order_id,))
        order = cursor.fetchone()

        if not order:
            await message.answer("Заказ не найден.")
            return

        # Проверяем, имеет ли пользователь право просматривать этот заказ
        cursor.execute("SELECT user_id FROM orders WHERE id=?", (order_id,))
        order_user_id = cursor.fetchone()[0]

        if order_user_id != message.from_user.id and not is_admin(message.from_user.id):
            await message.answer("У вас нет доступа к этому заказу.")
            return

        # Получаем товары из заказа (вам нужно будет создать таблицу order_items)
        cursor.execute("""
            SELECT p.name, p.price 
            FROM order_items oi 
            JOIN products p ON oi.product_id = p.id 
            WHERE oi.order_id=?
        """, (order_id,))
        items = cursor.fetchall()

        response = (
            f"📋 Детали заказа #{order[0]}\n"
            f"👤 ФИО: {order[6]}\n"
            f"📞 Телефон: {order[7]}\n"
            f"🚚 Способ: {'Доставка' if order[4] == 'delivery' else 'Самовывоз'}\n"
            f"🏠 Адрес: {order[8]}\n"
            f"📦 Статус: {order[2]}\n"
            f"📅 Дата: {order[3]}\n\n"
            f"🛒 Состав заказа:\n"
        )

        total = 0
        for item in items:
            response += f"• {item[0]} - {item[1]} руб.\n"
            total += item[1]

        response += f"\n💳 Итого: {order[1]} руб. (включая доставку: {order[5]} руб.)"

        await message.answer(response)

    except Exception as e:
        logger.error(f"Error showing order details: {e}")
        await message.answer("⚠️ Произошла ошибка при загрузке деталей заказа.")


# Добавим обработчик для кнопки возврата в админ-панель
@dp.callback_query(F.data == "back_to_admin")
async def back_to_admin_panel(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.menu)
    await callback.message.delete()
    await callback.message.answer("👑 Админ-панель:", reply_markup=admin_menu_kb())
    await callback.answer()

# Запуск бота
async def main():
    await log_action("🚀 Бот запущен!")
    logger.info("Starting bot...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
