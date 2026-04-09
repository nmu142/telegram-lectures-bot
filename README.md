# Telegram Lectures Bot

بوت تيليجرام لإدارة وتوزيع المحاضرات (واجهة عربية بالكامل)، مبني على `python-telegram-bot` v22 و `aiosqlite` مع بنية غير متزامنة (async).

## المتطلبات

- Python 3.11+
- حساب بوت من [@BotFather](https://t.me/BotFather)

## الإعداد محليًا

1. أنشئ بيئة افتراضية وثبّت الاعتماديات:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

2. عيّن المتغيرات البيئية (لا ترفع القيم الحقيقية إلى Git):

```bash
set BOT_TOKEN=your_bot_token
set ADMIN_ID=your_numeric_telegram_user_id
```

على Linux/macOS استخدم `export` بدل `set`.

3. التشغيل:

```bash
python bot.py
```

## النشر على Railway

1. اربط المستودع أو ارفع المشروع.
2. في **Variables** أضف على الأقل:
   - `BOT_TOKEN` — رمز البوت من BotFather
   - `ADMIN_ID` — معرفك الرقمي في تيليجرام (الأدمن الرئيسي)
3. اختياري: `DATABASE_PATH` — مسار ملف SQLite الثابت (مثلاً على وحدة تخزين مرفقة إن لزم).
4. أمر التشغيل: `python bot.py` (وضع polling).

ملاحظة: عند إعادة النشر، احتفظ بنفس `DATABASE_PATH` أو بملف قاعدة البيانات كنسخة احتياطية من لوحة الأدمن حتى لا تفقد البيانات.

## الأوامر

- `/start` — القائمة الرئيسية
- `/subjects` — المواد
- `/search` — بحث
- `/help` — المساعدة
- `/admin` — لوحة الأدمن (للمصرّح لهم فقط)

## الأمان

- لا تضع `BOT_TOKEN` أو معرفات حساسة في الكود أو في المستودع.
- استخدم متغيرات البيئة أو إعدادات Railway السرية فقط.

## الهيكل

- `bot.py` — نقطة الدخول والتسجيل
- `config.py` — الإعدادات من البيئة
- `db.py` — طبقة قاعدة البيانات (async)
- `handlers/` — معالجات المستخدم والأدمن
- `keyboards/` — لوحات المفاتيح
- `services/` — بحث، مفضلة، رفع دفعات، بث
- `utils/` — مساعدات ومزخرفات

## الرخصة

استخدم المشروع وفق احتياجك؛ تأكد من الامتثال لشروط تيليجرام وسياسات المحتوى.
