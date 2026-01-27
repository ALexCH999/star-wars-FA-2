# Star Wars — FastAPI + PostgreSQL (локальный проект)

Краткая инструкция:

1. Скопируйте `.env.example` -> `.env` и заполните `DATABASE_URL` и `ADMIN_PASSWORD`.

2. Создайте виртуальное окружение и установите зависимости:

    python -m venv venv
    source venv/bin/activate   # на Windows: venv\Scripts\activate
    pip install -r requirements.txt

3. Запустите приложение (локально):

    uvicorn main:app --reload

4. Откройте в браузере: http://127.0.0.1:8000
   Админка: http://127.0.0.1:8000/admin  (вход по паролю из ADMIN_PASSWORD)

Примечания:
- Изображения сохраняются в `static/uploads/...` (не в БД).
- Для разработки таблицы создаются автоматически при первом запуске.
