# Production backend (P081)

`manage.py runserver` development serveridir; production traffic uchun
ishlatilmaydi. Bu repository WSGI processni pinned `gunicorn==23.0.0` bilan
ishga tushiradi. Gunicornni systemd, Supervisor yoki hosting process manager
boshqarishi mumkin; ular restart va environment secretlarini repositorydan
tashqarida saqlaydi.

## Process

`docs/production.env.example`dagi qiymatlarni hosting environment yoki secret
storega moslang, migratsiyani bitta release jarayonida qo'llang, so'ng processni
ishga tushiring:

```sh
python -m pip install -r requirements.txt
python manage.py migrate --noinput
gunicorn --config gunicorn.conf.py root.wsgi:application
```

`GUNICORN_BIND` default bo'yicha `127.0.0.1:8000`; bu backendni lokal
reviewable qiladi va hali public port ochmaydi. Worker/thread/time-out qiymatlari
ham environment orqali o'zgaradi. Bir necha process manager faqat bitta
Gunicorn master processni boshqarsin; migrationni har worker startida ishlatmang.

## Production security

`DJANGO_DEBUG=false` bo'lganda `DJANGO_SECRET_KEY` hamda bo'sh bo'lmagan,
aniq `DJANGO_ALLOWED_HOSTS` majburiy. Session va CSRF cookie'lari secure,
`HttpOnly`/`Lax` bo'ladi; content type, referrer va frame siyosatlari ham
yoqilgan. Cross-origin admin/form POST kerak bo'lsa faqat to'liq HTTPS originni
`DJANGO_CSRF_TRUSTED_ORIGINS`ga kiriting.

Celery broker va timetable cache Redis originlari alohida bo'lishi ham
majburiy; settings bir xil origin topilsa processni ishga tushirmaydi. Natija
backendi va abuse cache uchun ham production network/ACL chegarasini alohida
rejalang.

Production console email backend bilan ishga tushmaydi: `DJANGO_DEFAULT_MAILER_BACKEND`
orqali SMTP yoki tashkilotning boshqa production backendini, uning credentialini
esa secret store orqali bering.

P081 yakunida TLS reverse proxy yo'q. Shu sabab `DJANGO_TRUST_PROXY_SSL_HEADER`,
`DJANGO_SECURE_SSL_REDIRECT` va HSTS defaultda o'chirilgan: ularni TLS ishlashi
tasdiqlanmasdan yoqish redirect loop yoki noto'g'ri security holatiga olib
kelishi mumkin. P081-A Nginx/TLS tugagach proxy faqat ishonchli tarmoqdan
`X-Forwarded-Proto` uzatadi, so'ng uchalasini production environmentda yoqing.
P081-B private S3 storage alohida scope'da.

Private S3-compatible adapter, credential environmenti va signed-download
qoidalari [P081-B storage qo'llanmasida](PRIVATE_S3_STORAGE.md) yozilgan.
Health, request log va Celery/outbox operational commandlari
[P082 operations qo'llanmasida](OPERATIONS.md) yozilgan.

## Deployment check

Production environment bilan tekshiring:

```sh
python manage.py check --deploy
gunicorn --check-config --config gunicorn.conf.py root.wsgi:application
```

P081 holatida `check --deploy` HSTS, HTTPS redirect va secure proxy haqidagi
warninglarni beradi; ular P081-A bajarilmaguncha ochiq risk sifatida qoladi.
Secret, `ALLOWED_HOSTS`, cookie va asosiy browser security settingsi esa ushbu
bosqichda tekshiriladi.
