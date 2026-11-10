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

## Sintetik development ma’lumotlari

`seed_school` faqat `DJANGO_ENV=development` va `DJANGO_DEBUG=true` yoki
`DJANGO_ENV=test` muhitida ishlaydi. U bitta 2026–2027 o‘quv yili, to‘rt chorak,
7-A va 7-B sinflari, olti sintetik foydalanuvchi, uch fan, kurslar hamda
biriktirishlarni yaratadi. Barcha pupil va teacher ma’lumotlari o‘ylab topilgan;
ular haqiqiy shaxslarni anglatmaydi va haqiqiy pupil importi emas.

Yangi seed hisoblari uchun parollarni `.env`ga yoki process environment’ga
`SEED_TEACHER_ONE_PASSWORD`, `SEED_TEACHER_TWO_PASSWORD`,
`SEED_PUPIL_ONE_PASSWORD`, `SEED_PUPIL_TWO_PASSWORD`,
`SEED_PUPIL_THREE_PASSWORD` va `SEED_PUPIL_FOUR_PASSWORD` nomlari bilan kiriting.
Qiymatlarni kodga, logga yoki repository’ga yozmang. Mavjud seed hisoblarining
parollari qayta ishga tushirishda o‘zgartirilmaydi.

```sh
python manage.py seed_school
```

Komanda transaction ichida ishlaydi va qayta ishga tushirilganda takroriy
asosiy yozuvlar yaratmaydi. U foydalanuvchilarning parollarini Django’ning
`create_user()` manageri orqali hash qilib saqlaydi.

## Teacher capability’larini berish

`pupil` va `teacher` — biznes rollari; `manage_accounts` va `manage_academics`
esa tanlangan teacherga alohida beriladigan Django permissionlaridir. Oddiy
teacher hech qanday operator capability’iga avtomatik ega bo‘lmaydi.
Superuser — Django’ning texnik boshqaruv identifikatori va permissions
cheklovlaridan ozod; u operator teacher capability’si bilan bir xil emas.

Faol superuser operator mavjud faol teacherga bitta aniq capability berishi
mumkin. `--reason` majburiy; yangi grant actor, target, capability, sabab va
vaqt bilan `CapabilityGrantAudit` jadvalida qayd etiladi. Takroriy ishga
tushirish permission yoki audit yozuvini takrorlamaydi. Command parol yaratmaydi
yoki tarqatmaydi; actor nomi audit atributidir, shu bois commandni faqat
ishonchli texnik operator ishga tushirishi kerak.

```sh
python manage.py bootstrap_teacher \
  --actor <superuser-username> \
  --target <existing-teacher-username> \
  --capability manage_accounts \
  --reason "Approved by school operator"
```

`--capability` qiymati `manage_accounts` yoki `manage_academics` bo‘lishi
mumkin. Pupil, faol bo‘lmagan teacher yoki faol bo‘lmagan/superuser bo‘lmagan
actor bilan command rad etiladi. Profil PATCH capability maydonlarini qabul
qilmaydi.

## Account registration va parolni tiklash

`POST /api/v1/accounts/` ochiq registration endpointidir: JWT yoki CSRF talab
qilinmaydi. `username`, `first_name`, `last_name`, `role` (`pupil` yoki
`teacher`) va foydalanuvchi tanlagan `password` majburiy; `preferred_language`
ixtiyoriy. Parol Django validatorlaridan o‘tib hash shaklida saqlanadi.
`is_staff`, `is_superuser`, capability va enrollment kabi privilege maydonlari
rad etiladi. Javob parolni qaytarmaydi. Ro‘yxatdan o‘tish ochiq bo‘lgani uchun
production’da signup siyosati, abuse monitoringi va rate limitni deployment
qatlamida belgilang.

`POST /api/v1/accounts/{id}/reset-password/` autentifikatsiya va
`manage_accounts` huquqini talab qiladi; bo‘sh JSON body bilan ishlaydi.
`200` javobda yangi vaqtinchalik parol bir marta qaytariladi va
`Cache-Control: no-store` qo‘yiladi. Parol yangilangach eski credential va
tokenlar keyingi tekshiruvda bekor qilinadi, `must_change_password=True`
yoqiladi. Ikkala amal `AccountLifecycleAudit`da actor, target, amal va vaqtni
qayd etadi; parol auditga/logga yozilmaydi.

Registration so‘roviga autentifikatsiya kerak emas. Parolni tiklash uchun
`manage_accounts` capability’li foydalanuvchining JWT access tokenidan
foydalaning:

```sh
curl -sS -H 'Content-Type: application/json' \
  --data '{"username":"new.pupil","first_name":"Aziza","last_name":"Karimova","role":"pupil","password":"<strong-password>"}' \
  http://127.0.0.1:8000/api/v1/accounts/
curl -sS -H 'Authorization: Bearer <access-token>' \
  -H 'Content-Type: application/json' \
  --data '{}' \
  http://127.0.0.1:8000/api/v1/accounts/123/reset-password/
```

Haqiqiy parolni shell history’ga yozmang. Reset javobidagi vaqtinchalik
parolni xavfsiz kanalda yetkazing; response’ni saqlamang yoki loglamang.
Account yaratish enrollment yaratmaydi; u alohida jarayon.

## Kurslarni o‘qish API’si

- `GET /api/v1/courses/` — foydalanuvchiga tegishli kurslar ro‘yxati.
- `GET /api/v1/courses/{id}/` — shu ro‘yxatdagi bitta kurs.

Ikkala endpoint ham autentifikatsiyani talab qiladi. Pupil faol yoki yopilgan
o‘z enrollment’i bor kurslarni, teacher esa faol yoki tarixiy o‘z assignment’i
bor kurslarni ko‘radi. Bu course read scope; tarixiy baholar uchun alohida
endpoint mavjud emas. Yozuvchi business endpointlarda pupil uchun faol
enrollment, teacher uchun faol assignment va `can_grade=True` talab qilinadi.
Javob fan kodi/nomi, sinf darajasi/bo‘limi, teacher display nomi va o‘quv yili
yorlig‘ini o‘z ichiga oladi; user profili yoki credential maydonlari
chiqarilmaydi. Muvaffaqiyatli javob `200`; autentifikatsiyasiz so‘rov `403`;
scope’dan tashqari detail ID `404` qaytaradi, scope’ga mos bo‘lmagan list
bo‘sh massiv qaytaradi. Misol javob:

```json
[
  {
    "id": 1,
    "subject": {"code": "SYN-MATH", "title": "Matematika"},
    "school_class": {"grade_level": 7, "section": "A"},
    "teacher_display": "Sami O‘qituvchi",
    "year": {"label": "2026-2027"}
  }
]
```

Testlar DRF `force_authenticate`dan foydalanadi. Lokal brauzer misoli:
`createsuperuser` interaktiv komandasi bilan `teacher` roli
berilgan superuser yarating; `manage.py shell`da uni `assign_teacher` service
orqali kursga biriktiring; so‘ng `/admin/`ga kirgan ayni brauzer sessiyasida
`http://127.0.0.1:8000/api/v1/courses/`ni oching. Superuser bo‘lish kurs
scope’ini chetlab o‘tmaydi.

## Session login

1. CSRF cookie va header uchun token oling:

   ```sh
   curl -sS -c /tmp/schoollms-cookies.txt \
     http://127.0.0.1:8000/api/v1/auth/csrf/
   ```

2. Javobdagi `csrfToken` qiymatini login so‘rovida `X-CSRFToken` headeriga
   yuboring; cookie jar session cookie’ni ham saqlaydi:

   ```sh
   curl -sS -b /tmp/schoollms-cookies.txt -c /tmp/schoollms-cookies.txt \
     -H 'Content-Type: application/json' \
     -H 'X-CSRFToken: <csrfToken>' \
     --data '{"username":"<username>","password":"<password>"}' \
     http://127.0.0.1:8000/api/v1/auth/login/
   ```

Parolni shell history yoki buyruqqa yozib qo‘ymang; lokal sinovda himoyalangan
interaktiv HTTP clientdan foydalaning. Login xatosi username mavjudligini
oshkor qilmaydi. Muvaffaqiyatli javob `200`, noto‘g‘ri credential `400`,
CSRF tokenisiz login `403`; token va JWT ishlatilmaydi.

## Joriy profil va logout

- `GET /api/v1/me/` joriy foydalanuvchining `id`, `username`, ism, familiya,
  role va til afzalligini qaytaradi.
- `PATCH /api/v1/me/` faqat `first_name`, `last_name` va
  `preferred_language`ni qabul qiladi. Username, role, staff flag, enrollment
  yoki noma’lum maydonni yuborish `400` qaytaradi.
- `POST /api/v1/auth/logout/` sessiyani bekor qiladi; CSRF token/header talab
  qilinadi. Logout’dan so‘ng private endpointlar `403` qaytaradi.

Uchala endpoint faqat joriy session user bilan ishlaydi. PATCH/POST so‘rovlari
uchun login’dan keyin `/api/v1/auth/csrf/`ni cookie jar bilan qayta chaqirib
CSRF cookie va token oling (login CSRF tokenini aylantiradi), so‘ng tokenni
`X-CSRFToken` headerida yuboring. Misol:

```sh
curl -sS -b /tmp/schoollms-cookies.txt -c /tmp/schoollms-cookies.txt \
  http://127.0.0.1:8000/api/v1/auth/csrf/
curl -sS -b /tmp/schoollms-cookies.txt -c /tmp/schoollms-cookies.txt \
  -H 'Content-Type: application/json' -H 'X-CSRFToken: <csrfToken>' \
  --data '{"first_name":"New name","preferred_language":"uz"}' \
  http://127.0.0.1:8000/api/v1/me/
curl -sS -b /tmp/schoollms-cookies.txt -c /tmp/schoollms-cookies.txt \
  -H 'X-CSRFToken: <csrfToken>' -X POST \
  http://127.0.0.1:8000/api/v1/auth/logout/
```

Autentifikatsiyasiz `/me/` va logout so‘rovlari `403`; ruxsatsiz profile
maydonlari `400`; muvaffaqiyatli profile GET/PATCH va logout `200` qaytaradi.

## Parolni almashtirish

- `POST /api/v1/me/change-password/` JSON’da `current_password` va
  `new_password` qabul qiladi. Joriy parol noto‘g‘ri yoki yangi parol Django
  password validatorlaridan o‘tmasa `400`; muvaffaqiyat `200` qaytaradi.
- Parol almashgach `must_change_password` o‘chadi. Hozirgi sessiya
  `update_session_auth_hash` bilan saqlanadi va sessiya kaliti aylantiriladi;
  boshqa sessiyalar eski parol hashiga bog‘langanligi uchun keyingi so‘rovda
  bekor qilinadi.
- `must_change_password=True` bo‘lsa business API’lar (jumladan course list
  va detail) `403` qaytaradi. Profil, parol almashtirish, logout, login, CSRF
  endpointlari ochiq qoladi. Cheklov serverda tekshiriladi, URL’ni bevosita
  chaqirish ham uni chetlab o‘ta olmaydi.

Parol endpointi ham session auth sabab CSRF header talab qiladi:

```sh
curl -sS -b /tmp/schoollms-cookies.txt -c /tmp/schoollms-cookies.txt \
  -H 'Content-Type: application/json' -H 'X-CSRFToken: <csrfToken>' \
  --data '{"current_password":"<current>","new_password":"<new-strong-password>"}' \
  http://127.0.0.1:8000/api/v1/me/change-password/
```

`/api/schema/`, `/api/docs/` va `/api/redoc/` hujjat endpointlari faqat
`DJANGO_DEBUG=true` bo‘lganda mavjud.
ReDoc JavaScript fayli `drf-spectacular-sidecar` orqali lokal xizmat qilinadi;
CDN yoki internetga ulanish talab qilinmaydi.
