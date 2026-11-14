# FT NIOC uploader and CAPTCHA lab

ابزار `captcha_lab.py` یک رابط محلی برای دریافت CAPTCHA در شبکه شرکت،
برچسب‌گذاری، آموزش مدل RGB ثابت‌طول و آزمون ورود است. نام کاربری و رمز عبور در
فایل یا دیتابیس ذخیره نمی‌شوند.

## نصب

برای آموزش TensorFlow از Python 3.10 تا 3.13 (ترجیحاً 3.12) استفاده کنید:

```powershell
py -3.12 -m venv C:\venvs\ftcaptcha
C:\venvs\ftcaptcha\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
```

مسیر venv عمداً کوتاه انتخاب شده است؛ نصب TensorFlow در بعضی مسیرهای بلند
Windows با خطای Long Path متوقف می‌شود.

سپس رابط را اجرا کنید:

```powershell
python captcha_lab.py
```

## جریان کار

1. در شبکه شرکت روی «بازکردن مرورگر» کلیک کنید.
2. «دریافت CAPTCHA» تصویر اصلی `data:image/...;base64` را می‌خواند؛ اگر
   در دسترس نباشد از screenshot همان عنصر استفاده می‌شود.
3. مقدار درست را با رقم فارسی یا انگلیسی وارد و نمونه را ذخیره کنید.
4. برای ارزیابی اولیه حداقل ۲۰ نمونه لازم است؛ برای استفاده واقعی حداقل
   ۵۰۰ نمونه متنوع توصیه می‌شود.
5. مدل را آموزش دهید و دقت کامل رشته را بررسی کنید.
6. پس از رسیدن به دقت قابل قبول، ورود آزمایشی را با حداقل confidence مناسب
   اجرا کنید. برنامه حداکثر سه بار تلاش می‌کند.

## جمع‌آوری و برچسب‌گذاری گروهی

1. مرورگر را باز کنید، تعداد Batch را تعیین کنید و «دریافت خودکار» را بزنید.
2. تصاویر و فایل `labels.csv` در پوشه نمایش‌داده‌شده در نوار وضعیت ذخیره می‌شوند.
3. `labels.csv` را با Excel باز کنید و فقط ستون `label` را تکمیل و با همان قالب CSV ذخیره کنید.
4. «ورود Batch برچسب‌خورده» را بزنید و پوشه Batch را انتخاب کنید.

برنامه پیش از ورود، کامل‌بودن همه برچسب‌ها، طول ارقام و وجود تصاویر را بررسی
می‌کند. دکمه «توقف» جمع‌آوری طولانی را پس از عملیات جاری متوقف می‌کند.

داده‌ها و مدل در `%LOCALAPPDATA%\ft-nioc-uploader` نگهداری می‌شوند و نباید
به Git اضافه شوند.

## ساخت نسخه Portable کامل

این نسخه TensorFlow و Chromium اختصاصی را همراه خود دارد و روی تین‌کلاینت به
Python، Edge یا نصب package نیاز ندارد. ساخت را روی Windows x64 و Python 3.12
انجام دهید:

```powershell
py -3.12 -m venv C:\venvs\ftcaptcha-build
C:\venvs\ftcaptcha-build\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File tools\build_portable.ps1
```

خروجی نهایی در `dist\FT-Captcha-Portable.zip` ساخته می‌شود. ZIP را روی
تین‌کلاینت استخراج و `Start-FT-Captcha.bat` یا `FT-Captcha.exe` را اجرا کنید.
آموزش مدل، برچسب‌گذاری، مرورگر و ورود خودکار همگی داخل همین بسته هستند.
