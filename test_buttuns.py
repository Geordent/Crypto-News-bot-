def test_buttons(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    logger.info(f"Получена команда /test_buttons от пользователя {user_id}")
    keyboard = [
        [InlineKeyboardButton("Тестовая кнопка 1", callback_data='test1')],
        [InlineKeyboardButton("Тестовая кнопка 2", callback_data='test2')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Это тестовые кнопки:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

def test_button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    action = query.data
    user_id = query.from_user.id

    if action == 'test1':
        query.edit_message_text(text="Вы нажали тестовую кнопку 1", parse_mode=ParseMode.MARKDOWN)
    elif action == 'test2':
        query.edit_message_text(text="Вы нажали тестовую кнопку 2", parse_mode=ParseMode.MARKDOWN)

# Регистрация обработчика тестовых кнопок
dispatcher.add_handler(CommandHandler("test_buttons", test_buttons))
dispatcher.add_handler(CallbackQueryHandler(test_button_handler, pattern='^test[12]$'))
