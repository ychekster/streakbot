"""API-сервер Telegram Mini App.

FastAPI-приложение, которое верифицирует Telegram `initData`, читает и пишет
данные через репозиторий бота (`bot.database.repository.Repository`) и отдаёт
фронтенду список привычек с историей выполнения за год.
"""
