import operator
from functools import reduce
import requests
import json
import os
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler, MessageHandler, filters


YONOTE_API_TOKEN=""
YONOTE_API_URL="https://app.yonote.ru/api"
TELEGRAM_BOT_TOKEN=""



headers = {
    'Authorization': f'Bearer {YONOTE_API_TOKEN}',
    'Content-Type': 'application/json'
}

# Связываем русские команды с английскими командами
commands_map = {
    "Выбор месяца": "/month",
    "Выбор тэгов": "/tags",
    "Сброс фильтров": "/reset",
    "Найти ссылки": "/find_links"
}

# Клавиатура с русскими командами
reply_keyboard = [
    ["Выбор месяца", "Выбор тэгов"], ["Сброс фильтров"],
    ["Найти ссылки"]
]

markup = ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=False)


####################################
# YONOTE API FUNCTIONS

def post(path, data):
    response = requests.post(f'{YONOTE_API_URL}/{path}', headers=headers, json=data)
    if response.status_code == 200:
        return response.json()
    else:
        print(f'Ошибка при запросе: {path}, статус: {response.status_code}')
        return None


def get_databases():
    databases = dict()
    data = post('collections.list', None).get('data')[0].get('documents')
    for document in data:
        if document.get('title') == 'Общий_стратегический_мониторинг\\':
            for db in document.get('children'):
                databases[db.get('title')] = db.get('id')
    return databases


# def get_db_tags(db_id):
#     response = post('documents.info', {"id": db_id})

#     # Если ответ пустой или содержит ошибку, возвращаем пустые данные
#     if not response:
#         print(f'Не удалось получить информацию о документе для ID: {db_id}')
#         return {}, {}

#     props = response.get('data', {}).get('document', {}).get('properties', [])

#     if not props:
#         print(f'Свойства документа не найдены для ID: {db_id}')
#         return {}, {}

#     props_with_options = list(filter(lambda x: len(x.get('options', [])) > 0, props))
#     tags_names = dict()
#     props_ids = dict()
#     for x in props_with_options:
#         options = dict()
#         for y in x.get('options'):
#             options[y.get('label')] = y.get('id')
#         tags_names[x.get('title')] = options
#     for x in props:
#         props_ids[x.get('title')] = x.get('id')
#     return tags_names, props_ids

def get_db_tags(db_id):
    response = post('documents.info', {"id": db_id})

    # Проверка успешного ответа
    if not response:
        print(f'Не удалось получить информацию о документе для ID: {db_id}')
        return {}, {}

    props = response.get('data', {}).get('document', {}).get('properties', [])

    # Проверка, есть ли свойства
    if not props:
        print(f'Свойства документа не найдены для ID: {db_id}')
        return {}, {}

    props_with_options = list(filter(lambda x: len(x.get('options', [])) > 0, props))
    tags_names = dict()
    props_ids = dict()

    # Добавляем проверку на наличие значений опций
    for x in props_with_options:
        options = dict()
        for y in x.get('options'):
            options[y.get('label')] = y.get('id')
        # Проверяем наличие тегов
        if options:
            tags_names[x.get('title')] = options
        else:
            print(f"Столбец '{x.get('title')}' не содержит тегов и будет пропущен.")
    for x in props:
        props_ids[x.get('title')] = x.get('id')

    return tags_names, props_ids


def get_url(row, props_ids):
    props = row.get('properties', {})
    title = row.get('title')

    yd_name = 'Ссылка на Яндекс диск'
    title_name = 'Название'
    document_name = 'Документ'

    title = title or props.get(props_ids.get(title_name, ''), '')
    title = title.replace('-', '\-').replace('(', '\(').replace(')', '\)').replace('.', '\.').replace(',', '\,')

    url = props.get(props_ids.get(yd_name, ''), '')
    url = url if isinstance(url, str) else url.get('url', '')

    # url = props.get(props_ids.get(document_name, ''), '')
    # url = "" if len(url) == 0 else f"https://imwes.yonote.ru{url[0].get('downloadURL', '')}"

    if url and title:
        return f'[{title}]({url})'
    if title:
        return title
    if url:
        return url
    return None


def get_rows(id, props_ids, tags: set):
    limit = 50
    i = 0
    res = []

    while True:
        data = {
            'limit': limit,
            'offset': i * limit,
            'parentDocumentId': id
        }
        page = post('database.rows.list', data).get('data', [])
        filtered_data = page if len(tags) == 0 else list(
            filter(lambda x: len(tags.intersection(
                set(
                    reduce(operator.iconcat, 
                        filter(lambda y: all(isinstance(item, (str, int)) for item in y),  
                            x.get('properties', {}).values()), [])
                )
            )) > 0, page)
        )

        # filtered_data = page if len(tags) == 0 else list(
        #     filter(lambda x: len(tags.intersection(set(
        #         reduce(operator.iconcat, filter(lambda y: isinstance(y, list), x.get('properties', {}).values()), [])
        #     ))) > 0, page)
        # )

        filtered_data = list(
            filter(lambda url: url is not None,
                   map(lambda x: get_url(x, props_ids), filtered_data))
        )
        res += filtered_data
        i += 1
        if len(page) < limit:
            break

    return res


####################################
# TG API FUNCTIONS

def get_user_data(context: CallbackContext, key: str, supplier):
    data = context.user_data.get(key, None)
    if not data:
        context.user_data[key] = supplier()
        data = context.user_data[key]
    return data


def reset_user_data(context: CallbackContext):
    context.user_data.clear()


def set_tag(context: CallbackContext, key: str, value: str):
    selected_tag = get_user_data(context, 'selected_tag', dict)
    if key in selected_tag.keys():
        if value not in selected_tag[key]:
            selected_tag[key].append(value)
    else:
        selected_tag[key] = [value]
    context.user_data['selected_tag'] = selected_tag


def unset_tag(context: CallbackContext, key: str, value: str):
    selected_tag = get_user_data(context, 'selected_tag', dict)
    if key in selected_tag.keys():
        if value in selected_tag[key]:
            selected_tag[key].remove(value)
        if len(selected_tag[key]) == 0:
            selected_tag.pop(key, None)
    context.user_data['selected_tag'] = selected_tag


def has_tag(context: CallbackContext, key: str, value: str):
    selected_tag = get_user_data(context, 'selected_tag', dict)
    return value in selected_tag.get(key, [])


def is_empty_tag(context: CallbackContext, key: str):
    selected_tag = get_user_data(context, 'selected_tag', dict)
    return key not in selected_tag.keys() or not selected_tag[key]


####################################
# Конструкторы кнопок

def build_month_keyboard(context: CallbackContext, months):
    keyboard = [[InlineKeyboardButton(f'{month} {"✅" if has_tag(context, "month", month) else ""}',
                                      callback_data=f"month:{month}")] for month in months]
    return InlineKeyboardMarkup(keyboard)


def build_options_keyboard(context: CallbackContext, key, tags):
    tags = sorted(list(set(tags.keys())))
    tags += ['назад ↩️']
    keyboard = [[InlineKeyboardButton(f'{tag} {"✅" if has_tag(context, key, tag) else ""}',
                                      callback_data=f"option:{tag[:10]}")] for tag in tags]
    return InlineKeyboardMarkup(keyboard)


def build_tags_keyboard(context: CallbackContext):
    tags = get_user_data(context, "tags", lambda: get_tags(context))
    keyboard = [[InlineKeyboardButton(tag, callback_data=f"tag:{tag[:10]}")] for tag in sorted(tags.keys())]
    return InlineKeyboardMarkup(keyboard)


####################################
# Служебные функции

def get_tags(context: CallbackContext):
    db = get_user_data(context, "db", get_databases)
    for db_name in db.keys():
        tags_names, _ = get_db_tags(db[db_name])
        if len(tags_names.keys()) > 0:
            return tags_names
    return {}


def get_selected_tags(context: CallbackContext):
    selected_tag = get_user_data(context, 'selected_tag', dict)
    tags = []
    keys = sorted(list(selected_tag.keys()))
    for k in keys:
        if k != 'month':
            for v in sorted(selected_tag[k]):
                tags.append((k, v))
    return tags


def search_for_links(context: CallbackContext):
    result = []
    db = get_user_data(context, "db", get_databases)
    months = sorted(db.keys())
    selected_tags = get_selected_tags(context)

    for db_name in months:
        if has_tag(context, 'month', db_name) or is_empty_tag(context, 'month'):
            try:
                tags_names, props_ids = get_db_tags(db[db_name])
                tags = set()

                for k, v in selected_tags:
                    tags.add(tags_names[k][v])

                result += get_rows(db[db_name], props_ids, tags)
            except Exception as e:
                print(f'Ошибка при поиске ссылок в базе {db_name}: {e}')
    return result


####################################
# Основные функции

async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        """Привет! Я бот, который помогает искать материалы в базе данных Института мировой военной экономики и стратегии.
""",
        reply_markup=markup)


async def reset(update: Update, context: CallbackContext):
    reset_user_data(context)
    await update.message.reply_text("Настройки тэгов и даты удалены")


async def choose_month(update: Update, context: CallbackContext) -> None:
    db = get_user_data(context, "db", get_databases)
    months = sorted(db.keys())

    if not months:
        await update.message.reply_text("Не удалось получить список по месяцам.")
        return

    await update.message.reply_text(f'Выберите месяц:',
                                    reply_markup=build_month_keyboard(context, months))


async def choose_tags(update: Update, context: CallbackContext) -> None:
    await send_tags_image_with_keyboard(update, context)
    await update.message.reply_text('Выберите тэги:', reply_markup=build_tags_keyboard(context))


async def print_tags(update: Update, context: CallbackContext) -> None:
    data = get_user_data(context, 'selected_tag', dict)
    data = json.dumps(data, indent=4, ensure_ascii=False).encode('utf8').decode()
    data = data.replace('-', '\-').replace('(', '\(').replace(')', '\)').replace('.', '\.').replace(',', '\,')
    await update.message.reply_text(f"```json\n{data}\n```", parse_mode='MarkdownV2')


async def find_links(update: Update, context: CallbackContext) -> None:
    await print_tags(update, context)
    await update.message.reply_text("Поиск...")

    result = search_for_links(context)

    await update.message.reply_text(f"Найдено статей: {len(result)}")

    for res in result:
        try:
            await update.message.reply_text(res, parse_mode='MarkdownV2')
        except:
            await update.message.reply_text(res)


####################################
# Функция для обработки текстовых сообщений

async def handle_message(update: Update, context: CallbackContext) -> None:
    user_text = update.message.text

    # Получаем команду из словаря
    command = commands_map.get(user_text)

    if command:
        # Выполняем соответствующую команду через контекст
        if command == "/month":
            await choose_month(update, context)
        elif command == "/tags":
            await send_tags_image_with_keyboard (update, context)
        elif command == "/reset":
            await reset(update, context)
        elif command == "/find_links":
            await find_links(update, context)


####################################
# Обработчики кнопок в меню

async def month_button(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()

    month = query.data[len('month:'):]
    db = get_user_data(context, "db", get_databases)
    months = sorted(db.keys())

    if has_tag(context, 'month', month):
        unset_tag(context, 'month', month)
    else:
        set_tag(context, 'month', month)

    await query.edit_message_text(text=f"Выберите месяц:",
                                  reply_markup=build_month_keyboard(context, months))


async def tag_button(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()

    tags = get_user_data(context, "tags", lambda: get_tags(context))
    selected_tag = query.data[len('tag:'):]

    # Находим полный тег по сокращенному значению
    for tag in tags:
        if tag.startswith(selected_tag):
            selected_tag = tag
            break

    reply_markup = build_options_keyboard(context, selected_tag, tags[selected_tag])

    # Используем edit_message_text только если в сообщении есть текст
    if query.message.text:
        await query.edit_message_text(text=selected_tag, reply_markup=reply_markup)
    # Если сообщения с текстом нет, добавляем новое сообщение с текстом и клавиатурой
    else:
        await query.message.reply_text(text=selected_tag, reply_markup=reply_markup)


async def option_button(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()

    tags = get_user_data(context, "tags", lambda: get_tags(context))
    selected_option = query.data[len('option:'):]
    selected_tag = query.message.text

    # Если selected_option равно 'назад ↩️', вернемся к выбору тегов
    if selected_option == 'назад ↩️':
        await query.edit_message_text('Выберите тэг:', reply_markup=build_tags_keyboard(context))
        return

    # Проверка и нахождение полного значения selected_option
    for tag in tags.get(selected_tag, {}):
        if tag.startswith(selected_option):
            selected_option = tag
            break

    # Если selected_tag не найден или значение некорректно, отправляем сообщение о проблеме
    if selected_tag is None or selected_tag not in tags:
        await query.message.reply_text('Ошибка: выбранный тег не найден. Попробуйте снова.')
        return

    # Проверяем, если тег уже выбран, убираем его, иначе добавляем
    if has_tag(context, selected_tag, selected_option):
        unset_tag(context, selected_tag, selected_option)
    else:
        set_tag(context, selected_tag, selected_option)

    # Обновляем сообщение с текущими выбранными опциями
    await query.edit_message_reply_markup(
        reply_markup=build_options_keyboard(context, selected_tag, tags[selected_tag])
    )



####################################
# Основная функция запуска бота

def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.add_handler(CallbackQueryHandler(month_button, pattern="month.*"))  # Обработчик нажатия на кнопки
    application.add_handler(CallbackQueryHandler(tag_button, pattern="tag.*"))  # Обработчик нажатия на кнопки
    application.add_handler(CallbackQueryHandler(option_button, pattern="option.*"))  # Обработчик нажатия на кнопки

    application.run_polling(allowed_updates=Update.ALL_TYPES)

async def send_tags_image_with_keyboard(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id

    # Если используем URL
    await context.bot.send_photo(
        chat_id=chat_id,
        photo="https://imgur.com/a/PxT9VPK",
        caption="Выберите теги:",
        reply_markup=build_tags_keyboard(context)
    )


if __name__ == "__main__":
    main()
