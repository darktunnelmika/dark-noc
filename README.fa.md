# DARK NOC v1.7.0 — مرکز فرمان NOC

[English](README.md) · **فارسی** · توسعه‌دهنده و پشتیبانی: **@mikakhadm**

DARK NOC یک پنل NOC مستقل برای مانیتورینگ سرورهای لینوکسی، مدیریت رخدادها،
کنترل سرویس‌ها، استقرار هماهنگ DARK Backhaul و اتصال SSH از داخل مرورگر است.

## نصب سریع

روی سرور مرکزی Hub با کاربر root اجرا کنید:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/darktunnelmika/dark-noc/main/install.sh) hub
```

روی هر سرور ایران یا خارج که باید به پنل متصل شود اجرا کنید:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/darktunnelmika/dark-noc/main/install.sh) node
```

نصاب سریع آخرین Release پایدار را دریافت می‌کند، SHA-256 فایل را بررسی می‌کند
و سپس نصب تعاملی را آغاز می‌کند.

## امکانات اصلی

- داشبورد زنده CPU، RAM، دیسک، Load، پهنای باند، Uptime و Connectionها
- نمایش IP کنار نام سرورها در Live Tunnel Matrix
- تشخیص خودکار نمونه‌های DARK Backhaul مدیریت‌شده توسط پنل
- ساخت هماهنگ تونل روی سرور ایران و خارج
- پروفایل‌های Stable، Balanced، Low Ping و Turbo
- Retry، حذف دوطرفه و Rollback خودکار در شکست نصب
- Incident، Auto-Heal و هشدار اختیاری تلگرام
- SSH مرورگری با رمزنگاری اطلاعات ورود و Pin شدن Host Key
- ترمینال کامل xterm.js با ANSI/VT، دو نشست هم‌زمان، تغییر اندازه PTY، جست‌وجو، کلیپ‌بورد، تمام‌صفحه و ذخیره لاگ
- نصب HTTPS خودکار با دامنه یا گواهی رمزنگاری‌شده برای IP
- ساخت خودکار نام کاربری و رمز اولیه قوی
- نصب جداگانه Hub و Node و حفظ تونل‌های قبلی سرور

در نسخه فعلی فقط افزونه **DARK Backhaul** در بخش تونل نمایش داده می‌شود.

## نصب Hub

فایل `DARK-NOC-HUB-v1.7.0.tar.gz` را روی سرور مرکزی قرار دهید:

```bash
tar -xzf DARK-NOC-HUB-v1.7.0.tar.gz
cd dark-noc-pro
chmod +x *.sh
sudo bash install-hub.sh
```

نصاب دامنه یا IP و پورت داخلی Hub را می‌پرسد. با Enter روی پورت، یک پورت آزاد
تصادفی ساخته می‌شود. پنل از بیرون همیشه با `https://DOMAIN` باز می‌شود.

## نصب Node

داخل پنل ابتدا **ADD SERVER** را بزنید و Token یک‌بارمصرف آن سرور را بردارید.
سپس فایل `DARK-NOC-NODE-v1.7.0.tar.gz` را روی همان Node اجرا کنید:

```bash
tar -xzf DARK-NOC-NODE-v1.7.0.tar.gz
cd dark-noc-node
chmod +x *.sh
sudo bash install-node.sh
```

آدرس HTTPS هاب و Token همان Node را وارد کنید. نصب Node فقط Agent و پیش‌نیازها
را نصب می‌کند و تونل‌های قبلی را تغییر نمی‌دهد.

## ارتقا

```bash
# روی Hub
sudo bash upgrade.sh hub

# روی Node با بسته جدید Node
sudo bash install-node.sh
```

قبل از ارتقای Hub از دیتابیس SQLite نسخه پشتیبان سازگار گرفته می‌شود و در صورت
خطا Rollback انجام خواهد شد.

## امنیت

- پورت داخلی Hub فقط روی `127.0.0.1` گوش می‌دهد.
- برای Node راه‌دور فقط HTTPS پذیرفته می‌شود.
- Token هر Node محرمانه و مستقل است.
- کلید یا رمز SSH هیچ‌وقت به مرورگر بازگردانده نمی‌شود.
- گزارش آسیب‌پذیری را عمومی نکنید؛ از بخش Security Advisory گیت‌هاب استفاده کنید.

## پشتیبانی

شناسه رسمی پروژه: **@mikakhadm**

برای گزارش باگ، Version، سیستم‌عامل، مراحل تکرار و Log سانسورشده را ارسال کنید.
هیچ‌وقت Password، Token، Private Key یا اطلاعات کامل سرورها را منتشر نکنید.
