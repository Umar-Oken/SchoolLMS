# School LMS backend

## Lokal ishga tushirish

PostgreSQL’ni operatsion tizimingizda o‘rnating va ishga tushiring yoki mavjud
PostgreSQL serveridan foydalaning. PostgreSQL o‘zining ma’lumotlar katalogida
ma’lumotni saqlaydi; saqlash va backup siyosati shu server tomonidan boshqariladi.
Django migratsiyalari sxemani yangilaydi, mavjud qatorlarni o‘chirib tashlamaydi.

1. Virtual environment yarating va faollashtiring:

   ```sh
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Runtime paketlarini o‘rnating, `.env.example`dan `.env` nusxa yarating va
   PostgreSQL ulanish parametrlarini o‘zingizning muhitga moslang. `.env`ga
   haqiqiy secret va parollarni kiriting, uni repositoryga qo‘shmang.

   ```sh
   pip install -r requirements.txt
   cp .env.example .env
   ```

   Lokal PostgreSQL uchun odatda `DB_HOST=127.0.0.1` va `DB_PORT=5432`
   ishlatiladi. Masofaviy server uchun `DB_HOST`, `DB_PORT`, `DB_NAME`,
   `DB_USER` va `DB_PASSWORD`ni tegishli qiymatlarga sozlang.
3. Django sozlamalari va DB ulanishini tekshirib, migratsiyalarni qo‘llang:

   ```sh
   python manage.py check
   python manage.py check --database default
   python manage.py migrate
   python manage.py showmigrations accounts
   python manage.py runserver
   ```

Production’da `DEBUG=false`, kuchli `DJANGO_SECRET_KEY`, aniq
`DJANGO_ALLOWED_HOSTS` va PostgreSQL ulanish qiymatlarini muhit o‘zgaruvchilari
orqali bering. P081’da Gunicorn production dependency sifatida o‘rnatilib,
`gunicorn root.wsgi:application` komandasi sozlangach, uni systemd, Supervisor
yoki hosting process manager orqali boshqaring. Development serverini
production’da ishlatmang.

P055’da Redis va Celery integratsiyasi sozlangandan keyin Redis’ni lokal servis
sifatida yoki mavjud server orqali ishga tushiring; ulanish manzilini
`REDIS_URL` environment variable orqali bering. Worker va Beat alohida
operatsion tizim processlari bo‘lib, har biri uchun quyidagi komandani alohida
ishga tushiring:

```sh
celery -A root worker -l info
celery -A root beat -l info
```

Beat’ning faqat bitta nusxasini ishga tushiring. Worker va Beat’ni systemd,
Supervisor yoki hosting process manager orqali boshqarish mumkin.

## P006: testlar va OpenAPI

Test va schema vositalarini development muhitiga o‘rnating:

```sh
pip install -r requirements-dev.txt
pytest apps/tests.py accounts/tests.py
python manage.py spectacular --validate --file /tmp/schoollms-openapi.yaml
```

Testlar sozlangan PostgreSQL bazasidan foydalanadi.

`/api/schema/`, `/api/docs/` va `/api/redoc/` hujjat endpointlari faqat
`DJANGO_DEBUG=true` bo‘lganda mavjud.
ReDoc JavaScript fayli `drf-spectacular-sidecar` orqali lokal xizmat qilinadi;
CDN yoki internetga ulanish talab qilinmaydi.
