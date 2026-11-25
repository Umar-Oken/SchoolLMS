# School LMS API qo‘llanmasi

## API asoslari

- Base URL: `http://127.0.0.1:8000/api/v1/`
- Swagger UI: `http://127.0.0.1:8000/api/docs/`
- OpenAPI schema: `http://127.0.0.1:8000/api/schema/`
- ReDoc: `http://127.0.0.1:8000/api/redoc/`
- Swagger/schema/ReDoc faqat `DJANGO_DEBUG=true` bo‘lganda yoqiladi.
- JSON requestlarda `Content-Type: application/json` yuboring.
- API JWT Bearer token ishlatadi. Login, logout va account-create uchun CSRF
  token talab qilinmaydi; boshqa API write so‘rovlari ham Bearer token bilan
  ishlaydi. Django admin/session hamda CSRF middleware himoyasi saqlanadi.
- Access va refresh tokenlar 1 kun amal qiladi.
- Autentifikatsiyasiz so‘rov `401`, permission rad etilganda `403`; noto‘g‘ri
  JSON yoki biznes validatsiyasi `400`;
  mavjud bo‘lmagan yoki foydalanuvchi scope’idan tashqari obyekt `404`;
  yopiq HTTP metodi `405` qaytaradi.
- Har qanday JSON xato responsei bir xil envelope’dan foydalanadi:
  `{"code":"...","message":"...","fields":{},"request_id":"..."}`.
  `code` frontendning tarmoqlanishi uchun stable qiymat; validation fieldlari
  `fields` ichida `message` va `code` bilan keladi. `request_id` support/log
  correlation uchun response `X-Request-ID` headerida ham bo‘ladi. Stale
  version va idempotency conflictlari `409`dagi o‘z custom code’ini saqlaydi;
  noto‘g‘ri `Content-Type` `415` qaytaradi. Traceback clientga berilmaydi.
- Sahifalangan list endpointlari `count`, `next`, `previous`, `results`
  shaklida; `page_size` 20 default va 100 maksimum. Pagination ishlatmaydigan
  endpointlar o‘zining hujjatlashtirilgan array/obyekt responseini saqlaydi.
- Datetime qiymatlari UTC aware ko‘rinishda saqlanadi va API kontrakti UTC’ni
  ishlatadi. `Asia/Tashkent` bo‘yicha lokal ko‘rsatish UI/API taqdimot
  qatlamining mas’uliyati; serverdagi saqlangan vaqt o‘zgartirilmaydi.
- Assignment vaqt oralig‘ida `available_at <= due_at <= closes_at`:
  `available_at` ish ochiladigan vaqt, `due_at` topshirish muddati,
  `closes_at` esa undan keyingi eng oxirgi yopilish vaqti. Ball `Decimal`
  sifatida saqlanadi; binary `float` aniq o‘nlik ballar uchun ishlatilmaydi.
- Rubrikada `Criterion` baholash mezoni va uning `max_points` chegarasini,
  `Level` esa aynan shu mezon uchun tavsif va ball darajasini ifodalaydi.
  Masalan, 6+3+1 balli mezonlar uchta alohida Criterion bo‘ladi. Level balli
  0 yoki undan katta, o‘z Criterion’ining `max_points` qiymatidan oshmaydi.
  Publish service har bir Criterion’da 0 balli Level borligini tekshiradi.
  Criterion’larning `max_points` yig‘indisini qatorlararo DB CHECK bilan
  tekshirib bo‘lmaydi; bu keyingi publish service vazifasi.

`POST /api/v1/attachments/` application darajasida 10 MiBdan katta body uchun
JSON `413 file_too_large` qaytaradi. Keyingi deploy’da reverse proxy ham
`client_max_body_size 10m` (yoki unga teng limit) bilan sozlanishi va proxy
rad etsa shu error envelope’ni qaytarishi yoki body’ni Django handlerigacha
o‘tkazishi shart; aks holda proxy HTML 413 contractdan tashqarida qoladi.

## Rollar va capability

Biznes rollari faqat `teacher` va `pupil`. `manage_accounts` va
`manage_academics` yangi rollar emas, tanlangan teacherlarga beriladigan
alohida Django permissions. Django superuser texnik identifikator bo‘lib,
role maydoniga baribir `teacher` yoki `pupil` kerak; superuser barcha Django
permissions’ga ega.

| Foydalanuvchi | Ruxsat |
|---|---|
| Anonim | Login va token refresh; qolgan himoyalangan API yo‘q |
| Pupil | O‘z profili; o‘z enrollment scope’idagi kurslar va published mavzular; katalogni o‘qish |
| Oddiy teacher | O‘z profili; biriktirilgan kurslar va mavzular; katalogni o‘qish; faol biriktirilgan kursga mavzu yozish |
| `manage_accounts` operator teacher | Yuqoridagilar va pupil/teacher account yaratish/parolini reset qilish |
| `manage_academics` operator teacher | Yuqoridagilar va yil, sinf, fan, course katalogini boshqarish |

Course list/detail teacher assignment va pupil enrollment bo‘yicha scope qilinadi.
Topic’ni faqat tegishli kursga biriktirilgan faol teacher yaratadi/tahrirlaydi;
pupil faqat published mavzuni o‘qiydi. Yopiq termga topic yaratish, tahrirlash
yoki publish qilish rad etiladi.

## Haftalik dars jadvali

`WeeklySlot` haftaning tanlangan kunida, `valid_from` va ixtiyoriy `valid_to`
oralig‘ida takrorlanadigan qoidani bildiradi; u ma’lum bir sanada o‘tgan dars
yoki davomat yozuvi emas. `start_time` va `end_time` maktabning mahalliy
devor-soati vaqtidir (`TimeField`); bu loyiha uchun maktab vaqt mintaqasi
`Asia/Tashkent`, UTC datetime emas. Kelajakda aniq sana uchun hodisa
yaratilganda sana va `Asia/Tashkent` vaqt mintaqasi birlashtirilib, UTC-aware
datetime’ga aylantirilishi kerak. Hozirgi qism bunday hodisalarni yaratmaydi.

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `GET/POST /api/v1/admin/rooms/` | Authenticated o‘qiydi; yaratish `manage_academics` talab qiladi; POST `{"name":"101-xona"}` | `200` ro‘yxat yoki `201 {"id":1,"name":"101-xona"}` | Takror nom/input `400`; write permission `403` |
| `GET/PUT/PATCH /api/v1/admin/rooms/<id>/` | Authenticated o‘qiydi; write `manage_academics` talab qiladi | Room; update uchun `{"name":"102-xona"}` | Input `400`; permission `403`; topilmasa `404` |
| `GET/POST /api/v1/admin/weekly-slots/` | Authenticated o‘qiydi; yaratish `manage_academics` talab qiladi | `200` ro‘yxat yoki `201` slot | Interval yoki teacher/course xatosi `400`; write permission `403` |
| `GET/PUT/PATCH /api/v1/admin/weekly-slots/<id>/` | Authenticated o‘qiydi; write `manage_academics` talab qiladi | Slot; PATCH’da o‘zgaradigan maydonlar | Interval yoki teacher/course xatosi `400`; permission `403`; topilmasa `404` |

Slot body misoli:

```json
{
  "course": 12,
  "teacher": 7,
  "room": 1,
  "weekday": 0,
  "start_time": "09:00:00",
  "end_time": "09:45:00",
  "valid_from": "2026-09-01",
  "valid_to": null
}
```

`weekday` 0–6: dushanba–yakshanba. `valid_to: null` bo‘lsa qoida muddatsiz
amal qiladi. Vaqt oralig‘i va amal qilish sanasi bo‘sh bo‘lmasligi hamda
boshlanishi tugashidan oldin bo‘lishi shart; teacher `teacher` rolida va
ko‘rsatilgan course’ga `TeachingAssignment` bilan biriktirilgan bo‘lishi kerak.
Oddiy teacher va pupil jadvalni faqat o‘qiydi.

Slotlar bir xil hafta kuni, kalendar sanalari kesishgan va vaqt oralig‘i
kesishgan bo‘lsa xona, teacher yoki sinf bo‘yicha konflikt hisoblanadi.
Sanalar `valid_from` va `valid_to`-gacha (inclusive) hisobga olinadi;
`valid_to: null` ochiq tugashni anglatadi. Vaqtlar yarim ochiq `[start, end)`
oralig‘ini bildiradi, shuning uchun `08:00–08:45` va `08:45–09:30` tutash
intervallari konflikt emas. Slot create/update bitta `TimetableLock` qatorini
`select_for_update()` bilan qulflab, konflikt tekshiruvi va yozishni bitta
transaction’da bajaradi; parallel so‘rovlar bir vaqtda konfliktli slot
yarata olmaydi.

`GET /api/v1/schedule/?from=2026-11-16&to=2026-11-30` `LessonSession`larni
so‘ralgan mahalliy sana oynasi bo‘yicha qaytaradi; ikkala sana ham kiritilishi
va oraliq 31 kalendar kundan oshmasligi kerak. Teacher faqat shu slotdagi o‘zi
bo‘lgan va dars sanasida kursga biriktirilgan sessionlarni; pupil esa o‘sha
sanada kursga enrollment’i amalda bo‘lgan sessionlarni ko‘radi. `starts_at` va
`ends_at` UTC datetime, `local_date` esa haftalik slotdan kelib chiqqan asl
occurrence sana, `scheduled_date` esa amaldagi sana.

Bu read endpointi 120 soniyalik (sozlanadigan, 60–300 soniya) timetable
cache’dan foydalanadi. Kalit request user IDsi, role, shu sana oynasidagi
course scope, interval va DBdagi timetable versionni o‘z ichiga oladi; shu
sabab bir foydalanuvchining jadvali boshqasiga berilmaydi. Weekly slot, yangi
materialize qilingan session yoki lesson exception o‘zgarsa version oshadi va
keyingi read yangi kalitdan DB ma’lumotini oladi. Redis cache miss yoki outage
bo‘lsa endpoint DB selector bilan ishlashda davom etadi. Production’da
`TIMETABLE_CACHE_URL` Celery brokeridan boshqa Redis instance bo‘lishi shart;
localhostdagi boshqa logical DB (`/2`) faqat development qulayligi.

Haftalik qoida recurrence’ni belgilaydi; bitta occurrence’ni bekor qilish yoki
ko‘chirish esa faqat o‘sha `LessonSession`ga istisno qo‘llaydi va boshqa
haftalardagi slot/sessionlarni o‘zgartirmaydi.

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `POST /api/v1/admin/lesson-sessions/<id>/exception/` | `manage_academics`; bekor qilish `{"action":"cancelled","reason":"..."}` | `200` — sessionning joriy holati | Noto‘g‘ri input `400`; conflict/course scope `400`; permission `403`; topilmasa `404` |
| `PUT /api/v1/sessions/<id>/attendance/` | Session sanasida shu course’ga biriktirilgan amaldagi teacher; JSON array | `200` — session ID va yuborilgan pupil holatlari | Noto‘g‘ri qator yoki bekor qilingan session `400`; ruxsat `403`; topilmasa `404` |
| `GET /api/v1/sessions/<id>/attendance/` | Shu session sanasidagi biriktirilgan teacher | `200` — course rosteri, joriy holatlar, tuzatish tarixi va summary | Ruxsat `403`; topilmasa `404` |
| `GET /api/v1/me/attendance/?from=&to=` | Faqat pupil; majburiy sana oynasi, ko‘pi bilan 31 kun | `200` — faqat o‘z sessionlari va summary | Noto‘g‘ri oyna `400`; ruxsat `403` |

Bekor qilish uchun session saqlanadi (`status=cancelled`), `ScheduleException`
audit yozuvi actor, sabab va oldingi/yangi holat bilan yoziladi. Ko‘chirishda
`action=moved`, `reason`, `scheduled_date`, `start_time`, `end_time` va
ixtiyoriy `teacher`, `room` yuboring. O‘rinbosar teacher ko‘chirish sanasida
shu course’ga biriktirilgan bo‘lishi shart. Yozuv `TimetableLock` ostida bitta
transaction’da P050’ning xona/teacher/sinf overlap qoidalari va o‘sha kundagi
sessionlar bilan solishtiriladi; konflikt bo‘lsa na audit, na session
o‘zgarishi saqlanadi. Istisno yozuvlarini update/delete qilish taqiqlangan.

### Dars davomatini belgilash

`PUT /api/v1/sessions/<id>/attendance/` faqat sessionning amaldagi teacher’i
uchun ochiq; ko‘chirilgan sessionda teacher session sanasidagi
`TeachingAssignment` bilan tasdiqlanadi. Bekor qilingan sessionga yozish
rad etiladi. Body — pupil va holat qatorlaridan iborat JSON array:

```json
[
  {"pupil": 42, "status": "present"},
  {"pupil": 43, "status": "excused"}
]
```

Holatlar `present`, `absent`, `late`, `excused` va `unmarked`. Har bir pupil
sessionning amaldagi (`scheduled_date`) sanasida shu course’ga enrollment’da
bo‘lishi kerak. Takroriy pupil yoki bitta noto‘g‘ri qator butun batch’ni bekor
qiladi; yuborilmagan pupilning mavjud davomat yozuvi o‘zgarmaydi va yangi
`absent` avtomatik yaratilmaydi. Birinchi marta yozilayotgan holat uchun
o‘zgarish sababi talab qilinmaydi. Mavjud holat o‘zgartirilganda `reason`
majburiy bo‘lib, eski/yangi holat, sabab va actor append-only auditga yoziladi;
holat o‘zgarmagan so‘rov audit yaratmaydi. Javob session ID va yuborilgan
pupillarning saqlangan holatlarini qaytaradi.

Yozuvlar va audit bitta transaction’da saqlanadi; status validatsiyasi yoki
enrollment tekshiruvi batch’ning biror qatorida xato bersa, qisman write
saqlanmaydi.

Pupil o‘z tarixini `GET /api/v1/me/attendance/?from=2026-11-01&to=2026-11-30`
orqali ko‘radi. Teacher yuqoridagi session GET orqali faqat o‘ziga biriktirilgan
darsning enrollment rosteri va davomatini ko‘radi; corrected status bilan
birga auditdagi oldingi/yangi holat, sabab, actor ID va vaqt qaytadi. Summary
joriy holatlarni sanaydi: `eligible_lessons` (pupil) yoki `eligible_pupils`
(teacher) maxraj, `marked_lessons`/`marked_pupils` esa `unmarked`dan boshqa
holatlar soni. Pupil summary uchun dars sanasida enrollment’i bo‘lgan va
bekor qilinmagan har bir session maxrajga kiradi; teacher session summary’da
bekor qilinmagan dars rosteri hisoblanadi. Bekor qilingan dars pupil tarixidan
chiqariladi; teacher uni ko‘rishi mumkin, ammo summary qiymatlari nol bo‘ladi.
Attendance qatori yo‘qligi va `unmarked` ikkalasi ham “hali belgilanmagan” deb
ko‘rsatiladi, lekin `present` hisoblanmaydi. Tuzatishdan keyin summary yangi
joriy statusni oladi; tarixiy audit yozuvlari esa o‘zgarmaydi. Bu summary
rasmiy davomat reytingi emas.

Materializatsiyani kerakli sana oynasi uchun qo‘lda ishga tushiring:

```sh
python manage.py materialize_lessons --from 2026-11-16 --to 2026-11-30
```

Oyna ko‘pi bilan 31 kun; bir xil commandni qayta bajarish `(slot, local_date)`
unikalligi sabab duplicate yaratmaydi. Generator faqat manual management
command/service; avtomatik Celery jadvali yo‘q.

Manbalar: [DRF generic views](https://www.django-rest-framework.org/api-guide/generic-views/),
[DRF validators](https://www.django-rest-framework.org/api-guide/validators/),
[PostgreSQL constraints](https://www.postgresql.org/docs/current/ddl-constraints.html),
[Django transactions](https://docs.djangoproject.com/en/5.2/topics/db/transactions/),
[PostgreSQL explicit locking](https://www.postgresql.org/docs/current/explicit-locking.html),
[DRF permissions](https://www.django-rest-framework.org/api-guide/permissions/),
[DRF testing](https://www.django-rest-framework.org/api-guide/testing/).

## Endpointlar

### Auth va profil

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `GET /api/v1/auth/csrf/` | Login shart emas; body yo‘q | `200 {"csrfToken":"<token>"}` | `403` |
| `POST /api/v1/auth/login/` | `{"username":"<username>","password":"<password>"}`; CSRF yo‘q | `200 {"access":"<access>","refresh":"<refresh>"}` | Noto‘g‘ri credential `401` |
| `POST /api/v1/auth/token/refresh/` | `{"refresh":"<refresh>"}`; CSRF yo‘q | `200 {"access":"<new-access>"}` | Yaroqsiz/blacklist refresh `401` |
| `POST /api/v1/auth/logout/` | Bearer access + `{"refresh":"<refresh>"}`; parol/CSRF yo‘q | `204`; refresh token blacklist qilinadi | Token yo‘q/noto‘g‘ri `401`; boshqa user refresh’i `403`; body noto‘g‘ri `400` |
| `GET /api/v1/me/` | Bearer access | `200 {"id":7,"username":"teacher01","first_name":"Ali","last_name":"Valiyev","role":"teacher","preferred_language":"uz"}` | Token yo‘q/yaroqsiz `401` |
| `PATCH /api/v1/me/` | Bearer access; `first_name`, `last_name`, `preferred_language`dan ixtiyoriy maydonlar | `200` — to‘liq yangilangan profil | Noma’lum yoki read-only maydon `400`; permission `403` |
| `POST /api/v1/me/change-password/` | Bearer access + `{"current_password":"<old>","new_password":"<new>"}` | `200 {"detail":"Parol muvaffaqiyatli o‘zgartirildi."}` | Joriy/yangi parol validatsiyasi `400`; token yo‘q `401` |

Profil username va role’ni o‘zgartirmaydi. Parol Django validatorlari bilan
tekshiriladi; parol o‘zgarganda password-hash claim’i mos kelmay qolib, avvalgi
JWT tokenlar bekor bo‘ladi.

Request body misollari:

`PATCH /api/v1/me/`:

```json
{"first_name": "Ali", "preferred_language": "uz"}
```

`POST /api/v1/me/change-password/`:

```json
{"current_password": "<joriy>", "new_password": "<yangi-kuchli-parol>"}
```

### Account boshqaruvi

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `POST /api/v1/accounts/` | Ochiq; auth/JWT va CSRF talab qilinmaydi; `username`, `first_name`, `last_name`, `role` (`pupil` yoki `teacher`), `password` majburiy; `preferred_language` ixtiyoriy | `201` — account profili; password qaytarilmaydi | Duplicate username, noto‘g‘ri role/password yoki privilege maydonlari `400` |
| `POST /api/v1/accounts/<id>/reset-password/` | Bearer access + `manage_accounts`; body `{}` | `200` — account va bir martalik `temporary_password` | Noto‘g‘ri body/staff account `400`; topilmasa `404`; ruxsat `403` |

Account create parolni required `password` maydonidan oladi va Django
`create_user()` / `set_password()` orqali hash qilib saqlaydi; javobda parol
qaytmaydi. Reset esa tasodifiy vaqtinchalik parol yaratadi, `must_change_password`
belgilaydi va uni faqat `Cache-Control: no-store` javobida bir marta ko‘rsatadi.
Public registration faqat pupil yoki teacher account yaratadi. `is_staff` va `is_superuser` kabi privilege maydonlarini yuborish qabul qilinmaydi. Telefon raqami yoki boshqa business role mavjud emas.

Request misollari:

```json
{"username": "pupil07", "first_name": "Aziza", "last_name": "Karimova", "role": "pupil", "password": "<kuchli-parol>", "preferred_language": "uz"}
```

`POST /api/v1/accounts/<id>/reset-password/`:

```json
{}
```

### Kurs va akademik katalog

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `GET /api/v1/courses/` | Bearer access; faqat qatnashadigan kurslar | `200 [{"id":12,"subject":{"code":"MATH","title":"Matematika"},"school_class":{"grade_level":7,"section":"A"},"teacher_display":"Ali Valiyev","year":{"label":"2026-2027"}}]` | Token yo‘q/yaroqsiz `401` |
| `GET /api/v1/courses/<id>/` | Bearer access; shu foydalanuvchi scope’i | `200` — kurs obyektining yuqoridagi shakli | Scope’dan tashqari/mavjud emas `404` |
| `POST /api/v1/admin/courses/` | `manage_academics` + Bearer access; `{"school_class":1,"subject":2}` | `201 {"id":12,"school_class":1,"subject":2}` | Duplicate/invalid `400`; permission `403` |
| `PUT/PATCH /api/v1/admin/courses/<id>/` | `manage_academics` + Bearer access; `school_class`, `subject` ID’lari | `200` — yangilangan course | Tarix bor parentni almashtirish/duplicate `400`; permission `403`; topilmasa `404` |
| `GET /api/v1/catalog/academic-years/` | Bearer access; teacher/pupil o‘qiydi | `200 [{"id":1,"label":"2026-2027","start_date":"2026-09-01","end_date":"2027-06-30","status":"active","terms":[{"id":1,"order":1,"start_date":"2026-09-01","end_date":"2026-11-30","status":"active"}]}]` | Token yo‘q/yaroqsiz `401` |
| `GET /api/v1/catalog/academic-years/<id>/` | Bearer access | `200` — bitta yil va uning `terms` ro‘yxati | Topilmasa `404` |
| `POST /api/v1/admin/academic-years/` | `manage_academics` + Bearer access; yil maydonlari va `terms` ro‘yxati | `201` — aggregate; termlarda `id`, `order`, sana va `status` bor | Sana/order/input xatosi `400`; permission `403` |
| `PUT/PATCH /api/v1/admin/academic-years/<id>/` | `manage_academics` + Bearer access; `terms` yuborilsa mavjudlari `id` bilan, yangilari IDsiz | `200` — yangilangan aggregate | Boshqa yil termi/duplicate order/sana xatosi `400`; permission `403`; topilmasa `404` |
| `GET /api/v1/admin/classes/` | Bearer access; teacher/pupil read-only | `200 [{"id":1,"year":1,"grade_level":7,"section":"A"}]` | Token yo‘q/yaroqsiz `401` |
| `POST /api/v1/admin/classes/` | `manage_academics` + Bearer access; `{"year":1,"grade_level":7,"section":"A"}` | `201` — yangi sinf | Duplicate/sinf darajasi invalid `400`; permission `403` |
| `GET /api/v1/admin/classes/<id>/` | Bearer access; teacher/pupil | `200` — sinf obyektining yuqoridagi shakli | Topilmasa `404` |
| `PUT/PATCH /api/v1/admin/classes/<id>/` | `manage_academics` + Bearer access; `year`, `grade_level`, `section` | `200` — yangilangan sinf | Duplicate yoki tarixli class identity’ni o‘zgartirish `400`; permission `403`; topilmasa `404` |
| `GET /api/v1/catalog/classes/` | Bearer access; tanlash uchun read-only | `200 [{"id":1,"year_id":1,"grade_level":7,"section":"A"}]` | Token yo‘q/yaroqsiz `401` |
| `GET /api/v1/catalog/classes/<id>/subjects/` | Bearer access; shu sinfda hali course bo‘lmagan faol fanlar | `200 [{"id":2,"code":"MATH","title":"Matematika"}]` | Sinf topilmasa `404` |
| `GET /api/v1/catalog/subjects/` | Bearer access; faqat arxivlanmagan fanlar | `200 [{"id":2,"code":"MATH","title":"Matematika"}]` | Token yo‘q/yaroqsiz `401` |
| `GET /api/v1/admin/subjects/` | Bearer access; teacher/pupil read-only | `200` — barcha Subject’lar, arxivlanganlar ham (`archived_at` bilan) | Token yo‘q/yaroqsiz `401` |
| `POST /api/v1/admin/subjects/` | `manage_academics` + Bearer access; `{"code":"MATH","title":"Matematika"}` | `201 {"id":2,"code":"MATH","title":"Matematika","archived_at":null}` | Duplicate code/invalid `400`; permission `403` |
| `GET /api/v1/admin/subjects/<id>/` | Bearer access; teacher/pupil | `200` — Subject, jumladan `archived_at` | Topilmasa `404` |
| `PUT/PATCH /api/v1/admin/subjects/<id>/` | `manage_academics` + Bearer access; `code`, `title` | `200` — yangilangan Subject | Duplicate/archived subject update `400`; permission `403`; topilmasa `404` |
| `POST /api/v1/admin/subjects/<id>/archive/` | `manage_academics` + Bearer access; body yo‘q | `200 {"id":2,"code":"MATH","title":"Matematika","archived_at":"<UTC-time>"}` | Permission `403`; topilmasa `404` |
| `GET /api/v1/catalog/teachers/` | `manage_academics` operator + Bearer access; faol teacherlar | `200 [{"id":7,"username":"teacher07","first_name":"Ali","last_name":"Valiyev"}]` | Boshqa user/ruxsatsiz `403` |
| `POST /api/v1/admin/teaching-assignments/` | `manage_academics` operator + Bearer access; `course`, `teacher`, `start_date`, optional `can_grade` | `201 {"id":8,"course":12,"teacher":7,"start_date":"2026-11-04","end_date":null,"can_grade":true}` | Pupil/self-grant/sana xatosi `400`; permission `403`; topilmagan FK `400` |
| `POST /api/v1/admin/teaching-assignments/<id>/close/` | `manage_academics` operator + Bearer access; `{"end_date":"2026-11-03"}` | `200` — assignment tarixiy qatori, end date bilan | Interval/sana xatosi yoki qayta yopish `400`; permission `403`; topilmasa `404` |

Operator scope `manage_academics` bilan assignmentlarni boshqaradi; bu o‘z-o‘zidan
grading vakolati emas. Grading scope faqat bugungi sana assignment intervaliga
kirsa va aynan shu assignmentda `can_grade=true` bo‘lsa beriladi. Yopish yoki
teacher almashtirish eski assignmentni o‘chirmaydi hamda uning teacher va
`can_grade` qiymatlarini o‘zgartirmaydi. Teacher, hatto `manage_academics`
huquqiga ega bo‘lsa ham, o‘ziga kurs biriktira olmaydi. Superuser operator
yo‘li biznes role qiymatidan mustaqil ishlaydi.

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `GET /api/v1/courses/<id>/pupils/` | Kursga faol biriktirilgan teacher yoki `manage_academics` operator | `200 [{"pupil_id":24,"first_name":"Aziza","last_name":"Karimova"}]` | Scope’dan tashqari yoki topilmagan kurs `404` |
| `POST /api/v1/admin/courses/<id>/sync-enrollments/` | `manage_academics` operator; body ixtiyoriy, yuborilsa `{}` | `200 {"course_id":12,"created_count":3}` | Noto‘g‘ri body `400`; permission `403`; arxivlangan/topilmagan course `404` |

`GET /api/v1/courses/<id>/pupils/` shu kursga bugun faol biriktirilgan
teacherga yoki `manage_academics` operatorga minimal roster beradi. Javob faqat
`pupil_id`, `first_name` va `last_name`ni o‘z ichiga oladi; faqat faol
enrollment’i va shu sinfdagi faol membership’i bugun amaldagi pupil’lar
chiqariladi. Pupil hamda boshqa kurs teacher’i rosterga kira olmaydi.

CourseOffering yaratish mavjud ClassMembership’lar uchun enrollmentni
avtomatik ochmaydi. Operator create javobidan keyin
`POST /api/v1/admin/courses/<id>/sync-enrollments/`ni chaqirishi kerak.
Body talab qilinmaydi; bo‘sh `{}` ham qabul qilinadi. Sync faqat bugun amaldagi faol membership’larni oladi va
membership `start_date`ini enrollment sanasi sifatida ishlatadi. Kurs uchun
istalgan enrollment tarixi, jumladan yopilgan enrollment bo‘lsa, u qayta
ochilmaydi yoki ustiga yozilmaydi; takroriy sync faqat hali enrollment tarixi
bo‘lmagan pupil uchun qator yaratadi. Bu alohida operator command bo‘lib,
CourseOffering create transaction’iga ulanmagan; javobdagi `created_count`
yangi ochilgan enrollmentlar soni.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/admin/courses/12/sync-enrollments/ \
  -H "Authorization: ******"
```

Year/Term va Subject uchun DELETE yo‘q. Year aggregate `terms` yuborilmasa,
mavjud choraklar o‘zgarishsiz qoladi; yuborilgan ro‘yxatda mavjud choraklar
`id` bilan, yangilari `id`siz beriladi. Ro‘yxatdan tushirib qoldirish o‘chirish
emas. Term statuslari `upcoming`, `active`, `closed`; `order` yil ichida
takrorlanmas va term sanalari yil chegarasida bo‘lishi shart. `PROTECT`
bog‘lanishlari tarixiy ma’lumotni saqlaydi. Subject arxivlanganda u yangi course selectoridan
yashiriladi, mavjud CourseOffering va unga bog‘liq tarix o‘zgarmaydi.
Course `PUT`da maydonlar to‘liq, `PATCH`da esa faqat o‘zgaradigan maydonlar
yuboriladi.

Yozuvchi katalog request namunalar:

`POST /api/v1/admin/academic-years/`:

```json
{
  "label": "2026-2027",
  "start_date": "2026-09-01",
  "end_date": "2027-06-30",
  "status": "upcoming",
  "terms": [
    {
      "order": 1,
      "start_date": "2026-09-01",
      "end_date": "2026-11-30",
      "status": "upcoming"
    }
  ]
}
```

`POST /api/v1/admin/classes/`:

```json
{"year": 1, "grade_level": 7, "section": "A"}
```

`POST /api/v1/admin/subjects/`:

```json
{"code": "MATH", "title": "Matematika"}
```

`POST /api/v1/admin/courses/`:

```json
{"school_class": 1, "subject": 2}
```

`POST /api/v1/admin/teaching-assignments/`:

```json
{
  "course": 12,
  "teacher": 7,
  "start_date": "2026-11-04",
  "can_grade": true
}
```

`POST /api/v1/admin/teaching-assignments/<id>/close/`:

```json
{"end_date": "2026-11-03"}
```

Operator endpointlarini bearer token bilan chaqirish:

```bash
curl -H "Authorization: Bearer <access-token>" \
  http://127.0.0.1:8000/api/v1/catalog/teachers/
curl -X POST http://127.0.0.1:8000/api/v1/admin/teaching-assignments/ \
  -H "Authorization: Bearer <access-token>" \
  -H "Content-Type: application/json" \
  -d '{"course":12,"teacher":7,"start_date":"2026-11-04","can_grade":true}'
```

### Pupilni sinfga biriktirish va ko‘chirish

`POST /api/v1/admin/class-memberships/` — `manage_academics` operatori yoki
superuser yuboradi. Body faqat `pupil`, `school_class` va aniq `start_date`
maydonlaridan iborat:

```json
{
  "pupil": 24,
  "school_class": 3,
  "start_date": "2026-11-06"
}
```

Muvaffaqiyatli javob `201` bo‘lib, membership `id`, `pupil`, `school_class`,
`start_date`, `end_date` va `status` maydonlarini qaytaradi. Pupil bo‘lmagan
target, ustma-ust interval, noma’lum maydon yoki noto‘g‘ri sana `400`;
ruxsatsiz foydalanuvchi `403`; topilmagan pupil/sinf `400` oladi.

`start_date` aniq effective date; API bugungi sanani o‘zi taxmin qilmaydi.
Transferda avvalgi membership va faol course enrollmentlar bir kun oldin
yopiladi, yangi sinfning faol kurslariga enrollment ochiladi. Eski qatorlar
o‘chirilmaydi yoki boshqa sinfga qayta bog‘lanmaydi. Ustma-ust tushadigan
retroaktiv sanalar rad etiladi. Barcha o‘zgarishlar P010 service ichidagi bitta
transaction’da bajariladi; enrollment yaratish xatosi membership va avvalgi
interval yopilishlarini ham rollback qiladi.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/admin/class-memberships/ \
  -H "Authorization: Bearer <access-token>" \
  -H "Content-Type: application/json" \
  -d '{"pupil":24,"school_class":3,"start_date":"2026-11-06"}'
```

### Mavzular

`manage_academics` katalog huquqi teacherga course assignment yoki grading
vakolatini bermaydi. Oddiy teacher faqat bugun faol assignment’i bor course’da
topic yaratishi va o‘z draft’ini patch/archive/publish qilishi mumkin.
Operator bo‘lgan teacherga bu course scope avtomatik berilmaydi.
Pupil faqat o‘z enrollment’idagi course’ning published topic’ini o‘qiydi;
draft detail va boshqa course mavzusi `404` qaytaradi. `POST` publish/archive
action’lari qatorni hard-delete qilmaydi. Course, year, class, subject,
assignment va membership uchun DELETE route yo‘q; tarix paydo bo‘lgach
year/class/subject parent identity’si o‘zgartirilmaydi.

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `GET /api/v1/courses/<course_pk>/topics/` | Bearer access; teacher o‘z draft/published, pupil faqat published | `200 [{"id":42,"course":12,"term":3,"title":"Kasrlar","objective":"Taqqoslash","order":1,"state":"published","published_at":"<UTC-time>","archived_at":null}]` | Course scope’dan tashqari `404` |
| `POST /api/v1/topics/` | Bearer access + faol assignment’li teacher; `course`, `term`, `title`, `objective`, `order` | `201` — draft topic (`published_at:null`) | Pupil `403`; invalid term/order/closed term `400`; begona course `404` |
| `GET /api/v1/topics/<id>/` | Bearer access; pupilga faqat published | `200` — Topic obyektining yuqoridagi shakli | Mavjud emas/scope tashqarisi/draft pupil uchun `404` |
| `PATCH /api/v1/topics/<id>/` | Bearer access + faol teacher owner; `term`, `title`, `objective`, `order`ning bir qismi | `200` — yangilangan Topic | Published/invalid/closed term `400`; foreign scope `404` |
| `POST /api/v1/topics/<id>/archive/` | Bearer access + faol teacher owner; body yo‘q | `200` — Topic, `archived_at` to‘ldiriladi | Permission `403`; scope/topilmasa `404` |
| `POST /api/v1/topics/<id>/publish/` | Bearer access + faol teacher owner; body yo‘q | `200` — `state:"published"` va `published_at` | Bo‘sh title/objective yoki closed term `400`; scope `404` |

Topic `course`, `state`, `published_at` client tomonidan create/update’da
boshqarilmaydi. PUT/DELETE route mavjud emas. Yopiq termdagi write `400`
qaytaradi; arxiv/publish mavjud qatorni o‘chirmaydi.

`PUT /api/v1/topics/<id>/completion/` joriy pupil uchun published mavzuni
o‘qilgan deb belgilaydi; so‘rov tanasi `{}`. `200` javobda
`{"topic":42,"kind":"read","completed_at":"<UTC-time>"}` qaytadi. Faqat faol
enrollment’li pupil belgi qo‘ya oladi. Teacher uchun `403`, boshqa kursga
yozilgan pupil, yopiq enrollment yoki draft mavzu uchun `404`; ortiqcha body
maydoni `400`. `GET /api/v1/topics/<id>/` pupilga o‘zining `completion`
qiymatini (`{"kind":"read","completed_at":"<UTC-time>"}` yoki `null`) qaytaradi;
teacher response’da bu maydon `null`.

Completion foizi mavzuni o‘qilgan deb belgilanganini anglatadi; bu bilim
bahosi yoki outcome mastery o‘lchovi emas. Har bir pupil/topic/read turi uchun
bitta DB yozuvi bo‘ladi. PUT’ni takror yuborish shu yozuvni qaytaradi va vaqt
belgisini o‘zgartirmaydi.

`POST /api/v1/topics/` request body:

```json
{
  "course": 12,
  "term": 3,
  "title": "Kasrlar",
  "objective": "Oddiy kasrlarni taqqoslash",
  "order": 1
}
```

`PATCH /api/v1/topics/<id>/`da shu maydonlardan faqat o‘zgaradiganlarini yuboring
(masalan, `{"title":"Kasrlarni taqqoslash"}`).

```bash
# Teacher: draft yaratish, tahrirlash va publish qilish
curl -X POST http://127.0.0.1:8000/api/v1/topics/ \
  -H "Authorization: ******" -H "Content-Type: application/json" \
  -d '{"course":12,"term":3,"title":"Kasrlar","objective":"Oddiy kasrlarni taqqoslash","order":1}'
curl -X PATCH http://127.0.0.1:8000/api/v1/topics/42/ \
  -H "Authorization: ******" -H "Content-Type: application/json" \
  -d '{"title":"Kasrlarni taqqoslash"}'
curl -X POST http://127.0.0.1:8000/api/v1/topics/42/publish/ \
  -H "Authorization: ******"

# Pupil: published mavzuni o‘qilgan deb belgilash va o‘z belgisini ko‘rish
curl -X PUT http://127.0.0.1:8000/api/v1/topics/42/completion/ \
  -H "Authorization: ******" -H "Content-Type: application/json" \
  -d '{}'
curl http://127.0.0.1:8000/api/v1/topics/42/ \
  -H "Authorization: ******"

# Teacher roster o‘qiydi; pupil faqat publish qilingandan keyin topicni ko‘radi
curl http://127.0.0.1:8000/api/v1/courses/12/pupils/ \
  -H "Authorization: ******"
curl http://127.0.0.1:8000/api/v1/courses/12/topics/ \
  -H "Authorization: ******"
```

### Assignment draftlari

Faol ravishda kursga biriktirilgan teacher o‘z topic’iga draft vazifa
yaratishi va faqat o‘zining scope’dagi draftini ko‘rishi mumkin. Pupil esa faqat
faol enrollment’i bor kurslardagi published topic va assignmentni ko‘radi.
Assignment `available_at` vaqtidan oldin ham sarlavha, shart, deadline, ball
hamda rubric metadata’sini ko‘rishi mumkin; bu hali topshirishga ruxsat degani
emas. `state`, `published_at` va rubric maydonlari client tomonidan
boshqarilmaydi.

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `POST /api/v1/assignments/` | ****** + faol assignment’li teacher; `topic`, `title`, `instructions`, `available_at`, `due_at`, `closes_at`, `max_score`, `max_attempts` | `201` — yaratilgan assignment, `state:"draft"` | Pupil `403`; begona/arxivlangan topic `404`; noto‘g‘ri sana yoki ortiqcha maydon `400` |
| `GET /api/v1/assignments/?course=<id>` | ****** + pupil; faqat faol enrollment va published topic/assignment | `200` — vazifalar, rubric, `availability_status`, `can_submit` | Teacher `403`; noto‘g‘ri course qiymati `400`; scope’dan tashqaridagi course bo‘sh ro‘yxat |
| `GET /api/v1/assignments/<id>/` | ****** + teacher o‘z drafti uchun yoki pupil published assignment uchun | `200` — role doirasidagi metadata; pupil uchun rubric va vaqt holati ham | Boshqa scope, draft topic/assignment yoki faol bo‘lmagan enrollment `404`; boshqa role `403` |
| `GET /api/v1/assignments/<id>/my-draft/` | ****** + joriy pupil, published assignment va faol enrollment | `200` — `submission_id`, `assignment`, `draft_text`, `draft_version`, `attachment_ids`; mavjud bo‘lmasa id `null`, text bo‘sh, version `0`, attachment list bo‘sh | Boshqa pupil/course, draft assignment yoki nofaol enrollment `404`; teacher `403` |
| `PUT /api/v1/assignments/<id>/my-draft/` | ****** + joriy pupil; `draft_text`, hozirgi `draft_version`, ixtiyoriy `attachment_ids` | `200` — saqlangan qoralama, oshirilgan version va attachment ID’lari | Eskirgan version `409`; begona/pending fayl, 3 tadan ko‘p yoki band fayl va ortiqcha `pupil` maydoni `400`; scope `404` |
| `POST /api/v1/assignments/<id>/submit/` | ****** + joriy pupil; `Idempotency-Key` header va joriy `draft_version` | `201` — `submission_id`, `revision_id`, `revision_number`, `draft_version`, `submitted_at`, `is_late`, `attachment_ids` | Boshqa input bilan ayni key `409`; stale version `409`; bo‘sh key `400`; bo‘sh ish, available_at’dan oldin, closes_at’dan keyin yoki attempt limiti `400`; scope/enrollment `404` |
| `POST /api/v1/submissions/<id>/return/` | ****** + shu kursga faol assigned teacher; `reason` (1–2000 belgi) | `201` — qaytarilgan `revision_id`, `revision_number`, ochilgan `draft_version`, `returned_at`, sabab | Begona teacher yoki submitted bo‘lmagan ish `404`; takror return yoki noto‘g‘ri sabab `400` |
| `PUT /api/v1/revisions/<id>/grade/` | ****** + revision kursiga faol `can_grade` teacher; `version`, `feedback`, barcha `criterion_scores` | `200` — draft Grade, server hisoblagan `total`, oshirilgan `version` va criterion ballari | Yetishmagan/begona/takror criterion yoki limitdan katta ball `400`; stale version `409`; pupil/begona teacher `403/404` |
| `POST /api/v1/revisions/<id>/grade/publish/` | ****** + shu kursning faol `can_grade` teacher; `grade_version`, `revision_version` | `200` — published Grade, server total, `published_at` va criterion ballari | Stale grade/revision version `409`; noto‘g‘ri revision yoki begona teacher `404`; to‘liq bo‘lmagan/limitdan tashqari rubric ballari `400` |
| `GET /api/v1/revisions/<id>/grade/` | ****** + revision egasi pupil, faol enrollment | `200` — faqat o‘zining published bahosi | Draft, boshqa pupil/revision yoki enrollment scope tashqarisi `404`; teacher `403` |
| `POST /api/v1/revisions/<id>/grade/regrade/` | ****** + shu kursning faol `can_grade` teacher; `expected_version`, barcha `criterion_scores`, majburiy `reason` | `200` — qayta hisoblangan va darhol published Grade | Sababsiz/noto‘g‘ri rubric `400`; stale version `409`; scope tashqarisi `404` |
| `GET /api/v1/me/gradebook/?course=<id>&term=<id>` | ****** + `request.user` pupil; faqat faol enrollment va published assignment | `200` — vazifa, holat, eng so‘nggi published grade va oldingi revision baholari | Noto‘g‘ri filter ID `400`; enrollment scope’dan tashqaridagi course bo‘sh ro‘yxat; teacher `403` |
| `GET /api/v1/courses/<id>/gradebook/` | ****** + kursga faol biriktirilgan teacher | `200` — topshirilgan ishlari bor pupil/submissionlar va eng so‘nggi revision/baho holati | Boshqa course `404`; pupil `403` |
| `GET /api/v1/assignments/<id>/submissions/` | ****** + vazifa kursiga faol biriktirilgan teacher | `200` — shu published vazifaning submitted/draft/graded/returned navbati | Boshqa teacher/vazifa `404`; pupil `403` |
| `PUT /api/v1/assignments/<id>/rubric/` | ****** + faol biriktirilgan teacher; to‘liq `criteria` va har bir criterion uchun to‘liq `levels` replacement’i | `200` — saqlangan rubric va child ID’lari | Noto‘g‘ri child ID, ball limiti yoki body `400`; pupil `403`; boshqa teacher/published vazifa `404` |
| `POST /api/v1/assignments/<id>/publish/` | ****** + faol biriktirilgan teacher; body kerak emas | `200` — `state:"published"`, `published_at`, `rubric_version:1` | To‘liq bo‘lmagan assignment/rubric `400`; pupil `403`; boshqa teacher yoki arxivlangan topic `404` |

`my-draft` GET mavjud qoralamani o‘qiydi, lekin hali yozuv bo‘lmasa DB qatorini
yaratmaydi va `{ "submission_id": null, "assignment": 42, "draft_text": "",
"draft_version": 0, "attachment_ids": [] }` qaytaradi. PUT’da GET’dan olingan
`draft_version`ni yuborish shart; muvaffaqiyatli saqlash bitta `Submission`
konteynerini yaratadi yoki yangilaydi va versionni bittaga oshiradi. Eskirgan
version `409` qaytaradi hamda yangiroq matnni bosib yozmaydi. Ixtiyoriy
`attachment_ids` berilganda u draft fayllari ro‘yxatini to‘liq almashtiradi;
maydon yuborilmasa amaldagi ro‘yxat saqlanadi, bo‘sh `[]` yuborilsa hammasi
draftdan ajratiladi. Ro‘yxat maksimum 3 ta takrorlanmagan ID’dan iborat bo‘lishi
kerak; har bir Attachment joriy pupilga tegishli va `clean` scan holatida
bo‘lishi shart. Bir fayl bir vaqtda faqat bitta draft yoki revisionga
biriktiriladi. Draftdan ajratilgach u boshqa draftga biriktirilishi mumkin;
revisionga biriktirilgan faylni qayta ishlatib yoki draftga ko‘chirib bo‘lmaydi.
Bog‘lanishlar va draft matn/version bitta transaction’da saqlanadi; bitta fayl
xatosi yoki stale version butun update’ni rollback qiladi.

Pupil faqat o‘zining faol enrollment’i bor kursdagi published assignmentga
biriktirilgan clean draft faylini yuklab oladi; available_at hali kelmagan
bo‘lsa ham draft metadata ko‘rinadi, ammo bu submit ruxsati emas. Teacher
assignmentni ko‘rish huquqiga ega bo‘lsa ham draft faylini ko‘rmaydi; private
download faqat assigned course scope’dagi revisionga bog‘langan fayl uchun
ochiladi. Pupil identity faqat tekshirilgan
session/JWT’dagi `request.user`dan olinadi; body’dagi `pupil` kabi noma’lum
maydonlar `400` qaytaradi. `Submission` tahrirlanadigan draftni saqlaydi;
`SubmissionRevision` final submitdagi o‘zgarmas snapshot. Unda yuborilgan draft
versiyasi, server vaqti va late belgisi ham saqlanadi.

`POST /api/v1/assignments/<id>/submit/` faqat `{"draft_version": 2}` qabul
qiladi. Assignment `available_at <= now <= closes_at` bo‘lishi kerak. `now`
`due_at`ga teng bo‘lsa `is_late=false`; undan keyin, `closes_at`ga teng vaqtni
ham qo‘shib, `is_late=true`. `closes_at`dan keyin submit yopiladi. Draftda
bo‘sh bo‘lmagan matn yoki kamida bitta clean fayl bo‘lishi shart.
`revision_number` `max_attempts`dan oshmaydi; ayni `draft_version`ni qayta
yuborish boshqa key bilan kelsa `409` qaytaradi. `Idempotency-Key` har submit
uchun majburiy, bo‘sh bo‘lmasligi va 255 belgidan oshmasligi kerak. Key
`pupil + assignment + key` doirasida ishlaydi; ayni key va ayni
`draft_version` oldingi `201` receipt’ni qaytaradi, boshqa version esa `409`
qaytaradi. Receipt va `revision_number` bir transaction’da DBga saqlanadi.
Shu sababli timeout ortidan deadline yopilgan bo‘lsa ham muvaffaqiyatli
so‘rovni ayni key bilan takrorlash yangi submit emas, oldingi receipt bo‘ladi.
Draftning yangi versiyasi yangi idempotency key bilan navbatdagi ruxsat etilgan
urinish bo‘lishi mumkin. Umumiy endpoint framework’i, return flow va
notification bu bosqichga kirmaydi.

Service avval Assignment, keyin Submission qatorini lock qiladi. Fayllar clean
holatda qayta tekshiriladi; draft matni, fayl bog‘lanishlari va server
belgilaydigan `submitted_at`/`is_late` bitta transaction’da revisionga
saqlanadi. Xato bo‘lsa revision ham, fayl ko‘chirish ham rollback bo‘ladi.
Keyinchalik draft tahrirlanishi mumkin, ammo yaratilgan revision matni va
fayllari o‘zgarmaydi.

Faol `can_grade` teacher `PUT /api/v1/revisions/<id>/grade/` orqali draft
baho yozadi. Body’da `version`, umumiy `feedback` va rubric’dagi har criterion
uchun aynan bitta `criterion_id`, `points` va ixtiyoriy feedback yuboriladi.
Begona, takror yoki tushib qolgan criterion, shuningdek criterion maksimumidan
katta ball `400` bilan rad etiladi. `version` eskirgan bo‘lsa `409`; birinchi
PUT `0` qabul qiladi va muvaffaqiyatda uni `1` qiladi. Grade draft holatida
qoladi va bu endpoint uni pupilga ko‘rsatmaydi. `Grade.total`ni server rubric
ballarining Decimal yig‘indisidan hisoblaydi; client `total` yuborsa strict
serializer rad etadi. Masalan, `5/6 + 2/3 + 1/1` natijasi `8.00/10.00`.

Teacher draftni `POST /api/v1/revisions/<id>/grade/publish/` orqali e’lon
qilganida `grade_version` va submitted snapshot’ning `revision_version`
(`submitted_version`) yuboradi:

```json
{
  "grade_version": 1,
  "revision_version": 1
}
```

Server faol `can_grade` huquq, revision va grade versiyalarini hamda rubricni
qayta tekshiradi: har criterion uchun yagona score bo‘lishi, score rubric
`max_points` chegarasida bo‘lishi va Grade total server hisoblagan yig‘indiga
teng bo‘lishi shart. Publish `state`, `published_at`, version va `GradeRevision`
audit yozuvini bitta transaction’da saqlaydi. Takror publish published Grade’ni
o‘zgartirmaydi va ikkinchi audit yozuvini yaratmaydi. Qayta baholash bu bosqichga
kirmaydi.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/revisions/91/grade/publish/ \
  -H "Authorization: ******" \
  -H "Content-Type: application/json" \
  -d '{"grade_version":1,"revision_version":1}'
curl http://127.0.0.1:8000/api/v1/revisions/91/grade/ \
  -H "Authorization: ******"
```

Pupil GET endpoint faqat o‘ziga tegishli, published baho va faol enrollment
doirasidagi revision’ni qaytaradi; draft yoki boshqa pupil bahosi `404`.

Faol `can_grade` teacher e’lon qilingan bahoni
`POST /api/v1/revisions/<id>/grade/regrade/` bilan qayta ko‘rib chiqishi
mumkin. `expected_version` joriy Grade versiyasiga teng bo‘lishi, har rubric
criterion’ga ball berilishi, client `total` yubormasligi ва `reason` хоосон
бўлмаслиги шарт:

```json
{
  "expected_version": 2,
  "criterion_scores": [
    {"criterion_id": 11, "points": "6.00", "feedback": "To‘liq dalil."},
    {"criterion_id": 12, "points": "1.00", "feedback": "Qisman izoh."},
    {"criterion_id": 13, "points": "0.00", "feedback": "Mezon bajarilmadi."}
  ],
  "reason": "Dalil mezoni qayta ko‘rib chiqildi."
}
```

Yangi natija shu transaction’da qayta publish qilinadi: pupil doim oxirgi
tasdiqlangan bahoni ko‘radi. `published_at` va `version` yangilanadi; har
qayta baholash `GradeRevision`ga eski/yangi total, umumiy feedback, grader va
vaqt, criterion ballari/feedback snapshot’larini hamda majburiy sababni
saqlaydi. Audit yozuvlari model darajasida tahrirlanmaydi/o‘chirilmaydi.
Eskirgan version `409`, begona teacher/revision `404`, noto‘g‘ri ball yoki
bo‘sh sabab `400` qaytaradi.

Pupilning shaxsiy jurnali `GET /api/v1/me/gradebook/` orqali olinadi. Ixtiyoriy
`course` va `term` query filterlari faqat joriy pupilning faol enrollment
doirasida ishlaydi. Har bir published assignment uchun `status`:

- `missing`: pupil hali revision yubormadi;
- `ungraded`: eng so‘nggi revision hali published bahoga ega emas;
- `returned`: eng so‘nggi revision teacher tomonidan qaytarilgan;
- `graded`: eng so‘nggi revision published bahoga ega.

`current_grade` eng yuqori revision raqamidagi published bahoni ko‘rsatadi;
undan oldingi published baholar `history` ro‘yxatida qoladi. Shu sabab yangi
revision hali baholanmagan bo‘lsa `status=ungraded`, lekin `current_grade`
avvalgi published natijani saqlab ko‘rsatishi mumkin. `current_grade: null`
published baho yo‘qligini, `total: "0.00"` haqiqiy nol bahoni bildiradi.
Criterion feedback har bir published grade ichida qaytariladi. Bu API chorak
yakuniy bahosini hisoblamaydi.

```bash
curl "http://127.0.0.1:8000/api/v1/me/gradebook/?course=42&term=3" \
  -H "Authorization: ******"
```

Faol kursga biriktirilgan teacher `GET /api/v1/courses/<id>/gradebook/` yoki
alohida vazifa uchun `GET /api/v1/assignments/<id>/submissions/` orqali
tekshirish navbatini oladi. Ikkala javob pupil ID/ism/familiyasi, assignment
ID/sarlavhasi va eng so‘nggi submitted revision raqami, vaqti, late belgisi
hamda draft/published Grade xulosasini beradi. Status `submitted` — baho yo‘q,
`draft` — baho draftda, `graded` — baho published, `returned` — oxirgi revision
qaytarilgan. Faqat revisioni bor submissionlar navbatga kiradi; draft matn,
attachment, email va ortiqcha profil maydonlari chiqarilmaydi. Faol biriktirilmagan
teacher `404`, pupil `403` oladi.

```json
{
  "version": 0,
  "feedback": "Yaxshi asoslangan javob.",
  "criterion_scores": [
    {"criterion_id": 11, "points": "5.00", "feedback": "Dalil aniq."},
    {"criterion_id": 12, "points": "2.00", "feedback": "Izoh bor."},
    {"criterion_id": 13, "points": "1.00", "feedback": "To‘liq."}
  ]
}
```

Teacher yuborilgan ishni `POST /api/v1/submissions/<id>/return/` orqali
`{"reason":"Dalillarni to‘ldiring."}` sababi bilan qaytaradi. Faqat faol
assigned teacher o‘z kursidagi published assignment uchun qaytara oladi;
submission’da kamida bitta revision bo‘lishi shart. Return revision yoki
fayllarni o‘chirmaydi: return yozuvi sabab va teacher/vaqtni saqlaydi; yangi
draft ochilganda avvalgi yuborilgan matn draftga ko‘chadi va version oshadi.
Revision attachment’lari revisionda qoladi, yangi draftga avtomatik
ko‘chirilmaydi. Submit qilingandan keyin teacher return qilmaguncha pupil draft
PUT qila olmaydi. Return deadline’ni uzaytirmaydi; `closes_at` o‘tgan bo‘lsa
qayta submit yopiq qoladi. `max_attempts` barcha revisionlarni hisoblaydi.

```json
{
  "draft_text": "Mening yechim qoralamam",
  "draft_version": 0,
  "attachment_ids": [17, 18]
}
```

```bash
curl http://127.0.0.1:8000/api/v1/assignments/1/my-draft/ \
  -H "Authorization: ******"
curl -X PUT http://127.0.0.1:8000/api/v1/assignments/1/my-draft/ \
  -H "Authorization: ******" \
  -H "Content-Type: application/json" \
  -d '{"draft_text":"Mening yechim qoralamam","draft_version":0,"attachment_ids":[17,18]}'
curl -X POST http://127.0.0.1:8000/api/v1/assignments/1/submit/ \
  -H "Authorization: ******" \
  -H "Idempotency-Key: pupil-42-assignment-1-submit-001" \
  -H "Content-Type: application/json" \
  -d '{"draft_version":1}'
curl -X POST http://127.0.0.1:8000/api/v1/submissions/7/return/ \
  -H "Authorization: ******" \
  -H "Content-Type: application/json" \
  -d '{"reason":"Dalillarni to‘ldiring."}'
```

Bir xil yuborishning xavfsiz retry namunasi:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/assignments/1/submit/ \
  -H "Authorization: ******" \
  -H "Idempotency-Key: pupil-42-assignment-1-submit-001" \
  -H "Content-Type: application/json" \
  -d '{"draft_version":1}'
```

Submit muvaffaqiyatli javobining namunasi (`201`):

```json
{
  "submission_id": 73,
  "revision_id": 91,
  "revision_number": 1,
  "draft_version": 2,
  "submitted_at": "2026-11-14T18:00:00Z",
  "is_late": false,
  "attachment_ids": [17]
}
```

Submit xatolarida noto‘g‘ri/missing idempotency key yoki bo‘sh ish `400`,
stale version yoki bir key’ni boshqa input bilan ishlatish `409`, enrollment
yoki assignment scope tashqarisida bo‘lish `404` qaytaradi. Muvaffaqiyatli
key’ni qayta yuborish oldingi `201` body’ni qaytaradi va ikkinchi revision
yaratmaydi.

`GET /api/v1/attachments/<id>/download/` clean file’ni egasi, pupilning o‘z
submission draft relation’i yoki assigned teacher’ning revision relation’i
doirasida beradi. Teacher draft attachment’iga `404` oladi. Published PDF
Resource uchun avvalgi course-scope qoidasi o‘zgarishsiz qoladi.

Pupil javobidagi `availability_status` qiymatlari: `upcoming` — `available_at`
hali kelmagan; `on_time` — mavjud va `due_at`gacha, shu jumladan aynan due
vaqtida; `late` — `due_at`dan keyin, `closes_at`gacha, shu jumladan aynan
close vaqtida; `closed` — `closes_at`dan keyin. `can_submit` shu vaqt
chegaralariga asoslangan maslahat qiymati, submit huquqining yakuniy tekshiruvi
emas; server submit paytida vaqt chegaralarini qayta tekshiradi. Kech
topshirishga avtomatik penalty bu bosqichga kirmaydi.

Datetime UTC-aware ISO 8601 formatida yuboriladi; `available_at <= due_at <=
closes_at` bo‘lishi shart. `max_score` musbat Decimal, `max_attempts` esa
1–32767 oralig‘idagi butun son. Response `id`, `topic`, asosiy maydonlar va
server belgilagan `state`ni qaytaradi; rubric bu endpointda yaratilmaydi.
Keyingi baholash modeli `Grade.total`ni server hisoblaydi; pupil request’idan
yakuniy total qabul qilinmaydi. `total=null` baho hali kiritilmaganini, `0.00`
esa baholangan ish uchun haqiqiy nol ballni bildiradi.
Publish faqat sarlavha va shart bo‘sh bo‘lmaganda, deadline’lar
`available_at <= due_at <= closes_at` tartibida, criterion’larning
`max_points` yig‘indisi assignment `max_score`iga teng va har bir criterion’da
kamida bitta 0 ballli level mavjud bo‘lganda muvaffaqiyatli bo‘ladi.
Assignment row lock ostida tekshiriladi va `state`, `published_at`,
`rubric_version=1` bitta transaction’da saqlanadi. Takroriy publish mavjud
`published_at`/version’ni o‘zgartirmaydi va yangi qator yaratmaydi. Published
assignment uchun oddiy update endpointi yo‘q, rubric replacement ham yopiq.
Assignment publish event notification yubormaydi. Deadline reminder alohida
Celery task’i orqali 24 soatlik oynaga kirganda yaratiladi.

Deadline’larni nazoratli almashtirish uchun API yo‘q. Operator service/command
orqali timezone-aware `available_at`, `due_at`, `closes_at` qiymatlarini birga
almashtiradi; haqiqiy o‘zgarish `deadline_version`ni oshiradi va oldingi
versiyadagi reminderlarni bekor qiladi:

```bash
.venv/bin/python manage.py change_assignment_deadlines \
  --assignment-id 42 \
  --available-at 2026-11-20T09:00:00Z \
  --due-at 2026-11-22T09:00:00Z \
  --closes-at 2026-11-23T09:00:00Z
```

Rubric `PUT` replacement hisoblanadi, PATCH emas. Har bir criterion va level’da
`id` bo‘lmasa yangi child yaratiladi; mavjud child’ni o‘zgartirish uchun uning
ID’si shu assignment (level bo‘lsa, aynan yuborilgan criterion) ichida bo‘lishi
kerak. So‘rovda qoldirilmagan criterion o‘chadi va uning level’lari cascade
orqali o‘chadi; yuborilgan criterion ichida qoldirilmagan level ham o‘chadi.
Mavjud ID saqlanib, child maydonlari yangilanadi. Barcha tekshiruv va yozuvlar
bitta transaction’da bajariladi; bitta qator xatosi butun replacement’ni
rollback qiladi. Published assignment rubricini o‘zgartirib bo‘lmaydi.

`PUT /api/v1/assignments/<id>/rubric/` body namunasi:

```json
{
  "criteria": [
    {
      "id": 3,
      "label": "Dalillar",
      "max_points": "6.00",
      "position": 0,
      "levels": [
        {
          "id": 5,
          "description": "To‘liq dalil keltiradi",
          "points": "6.00",
          "position": 0
        },
        {
          "description": "Qisman dalil keltiradi",
          "points": "3.00",
          "position": 1
        }
      ]
    }
  ]
}
```

Level ballari manfiy bo‘lmasligi va o‘z criterion’ining `max_points`idan
oshmasligi, sibling position’lar takrorlanmasligi kerak. Bo‘sh `criteria` barcha
criterion va level’larni o‘chiradi; bo‘sh `levels` shu criterion’ning barcha
level’larini o‘chiradi.

```bash
curl -X PUT http://127.0.0.1:8000/api/v1/assignments/1/rubric/ \
  -H "Authorization: ******" \
  -H "Content-Type: application/json" \
  -d '{"criteria":[{"label":"Dalillar","max_points":"6.00","position":0,"levels":[{"description":"To‘liq","points":"6.00","position":0}]}]}'
```

`POST /api/v1/assignments/`:

```json
{
  "topic": 42,
  "title": "Kasrlarni taqqoslash",
  "instructions": "Javobni tushuntirib yozing.",
  "available_at": "2026-11-10T09:00:00Z",
  "due_at": "2026-11-14T12:00:00Z",
  "closes_at": "2026-11-15T12:00:00Z",
  "max_score": "10.00",
  "max_attempts": 2
}
```

```bash
curl -X POST http://127.0.0.1:8000/api/v1/assignments/ \
  -H "Authorization: ******" \
  -H "Content-Type: application/json" \
  -d '{"topic":42,"title":"Kasrlarni taqqoslash","instructions":"Javobni tushuntirib yozing.","available_at":"2026-11-10T09:00:00Z","due_at":"2026-11-14T12:00:00Z","closes_at":"2026-11-15T12:00:00Z","max_score":"10.00","max_attempts":2}'
curl http://127.0.0.1:8000/api/v1/assignments/1/ \
  -H "Authorization: ******"
curl -X POST http://127.0.0.1:8000/api/v1/assignments/1/publish/ \
  -H "Authorization: ******"
```

### Kurs e’lonlari

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `GET /api/v1/courses/<course_id>/announcements/` | ******; teacher biriktirilgan kursning draft/published e’lonini, pupil o‘z kursidagi published e’lonni ko‘radi | `200` — e’lonlar ro‘yxati | Begona/bog‘lanmagan kurs `404` |
| `POST /api/v1/courses/<course_id>/announcements/` yoki `POST /api/v1/announcements/` | Faol assignment’li teacher; global create’da `course`; nested create’da body faqat `title`, `body` | `201` — `state=draft`, `published_at=null` | Pupil `403`; begona yoki arxivlangan course `404`; bo‘sh title `400` |
| `GET /api/v1/announcements/<id>/` | Teacher assigned course, pupil published e’lon va o‘z course’i | `200` — e’lon | Begona course/draft pupil `404` |
| `POST /api/v1/announcements/<id>/publish/` | Faol assignment’li teacher; body kerak emas | `200` — `state=published` | Pupil `403`; begona/arxivlangan course `404`; bo‘sh title `400` |

Publish e’lon holatini va `announcement.published` outbox event’ini bitta
transaction’da yozadi. Stable `announcement:<id>:published` dedup kaliti takroriy
publish’da ikkinchi event yaratmaydi. Bu bosqichda notification delivery
handler ayni kursning amaldagi recipientlariga in-app bildirishnoma yaratadi.

### In-app bildirishnomalar

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `GET /api/v1/notifications/?page=1&page_size=20` | ******; faqat joriy foydalanuvchi | `200` — `count`, `next`, `previous`, `results` bilan sahifalangan ro‘yxat | Noto‘g‘ri sahifa parametri `404` |
| `PATCH /api/v1/notifications/<id>/read/` | ******; faqat notification egasi | `200` — `read_at` to‘ldirilgan notification | Boshqa foydalanuvchi yoki topilmagan ID `404` |

Announcement publish event’i ayni kursning bugun faol enrollment’i bo‘lgan
pupillari va faol assignment’i bo‘lgan teacherlariga fan-out qilinadi. Har
notification `read_at=null` holatida unread; `read/` uni idempotent tarzda
o‘qilgan deb belgilaydi. `(recipient,event_key)` unique cheklovi takroriy
consumer delivery’da notification ko‘payishiga yo‘l qo‘ymaydi. Push/WebSocket,
email/SMS va deadline reminder yo‘q.

### Mavzu resurslari

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `GET /api/v1/topics/<topic_id>/resources/` | ******; teacher o‘z biriktirilgan mavzusidagi resurslarni, pupil o‘z published mavzusidagi published resurslarni ko‘radi | `200` — resurslar ro‘yxati | Boshqa course/mavzu yoki pupil uchun draft `404` |
| `POST /api/v1/topics/<topic_id>/resources/` | Faol assignment’li teacher; `kind`, `title`, `description`, `transcript_text`; PDF’da o‘z `clean` `attachment`, video/link’da ruxsatli `url` | `201` — `state=draft`, `version=1` resurs | Noto‘g‘ri attachment/URL yoki aralash input `400`; boshqa teacher/pupil mavzusi `404`/`403` |
| `GET /api/v1/resources/<id>/` | ******; teacher o‘z course scope’ida, pupil faqat o‘z published topic’idagi published resursni ko‘radi | `200` — resurs metadata | Draft, boshqa topic yoki topilmasa `404` |
| `POST /api/v1/resources/<id>/publish/` | Faol biriktirilgan teacher; body kerak emas | `200` — `state=published` | Mavzu draft yoki PDF attachment `clean` bo‘lmasa `400`; boshqa scope `404` |

PDF turi uchun faqat o‘ziga tegishli `clean` PDF `Attachment` biriktiriladi;
video va link turida faqat HTTPS `url` beriladi, attachment berilmaydi.
`RESOURCE_URL_ALLOWED_HOSTS` muhit sozlamasi hostname ruxsat ro‘yxati bo‘lib,
boshlang‘ich qiymatlari `youtube.com`, `youtu.be`, `vimeo.com`. Subdomenlarga
ruxsat beriladi; HTTP, ro‘yxatda bo‘lmagan host, credential bor URL va 443 dan
boshqa port rad etiladi. Server URL’ni so‘rov bilan ochmaydi. `state`, `version`
va topic ID server tomonidan boshqariladi. Resursni nashr qilishdan oldin topic
nashr qilingan bo‘lishi kerak. Javobda URL va accessibility uchun
`transcript_text` qaytadi; fayl bytes’i yoki public URL yaratilmaydi.
`description` qisqa izoh uchun, `transcript_text` esa video/link transkripti
yoki shunga teng accessibility matni uchun.

```bash
# Teacher: clean PDF biriktirish
curl -X POST http://127.0.0.1:8000/api/v1/topics/42/resources/ \
  -H "Authorization: ******" \
  -H "Content-Type: application/json" \
  -d '{"kind":"pdf","title":"Kasrlar materiali","description":"Dars PDF’i","attachment":17}'

# Teacher: ruxsatli provider URL’ini draft video resurs sifatida qo‘shish
curl -X POST http://127.0.0.1:8000/api/v1/topics/42/resources/ \
  -H "Authorization: ******" \
  -H "Content-Type: application/json" \
  -d '{"kind":"video","title":"Kasrlar videosi","url":"https://youtu.be/example123","transcript_text":"Videodagi matn"}'

# Mavzu publish qilingandan keyin resource publish qilinadi
curl -X POST http://127.0.0.1:8000/api/v1/resources/8/publish/ \
  -H "Authorization: ******"

# Pupil faqat published resurslarni ko‘radi
curl http://127.0.0.1:8000/api/v1/topics/42/resources/ \
  -H "Authorization: ******"
```

Bu repository’da task yoki grade yozish endpointlari hozircha mavjud emas.
Ular qo‘shilganda yopiq term siyosatini chetlab o‘tmaslik uchun
`academics.services.ensure_term_open()` shared guard’ini tegishli service
ichida chaqirish talab qilinadi.

### Savol banki: single-choice va essay

`Question` savol matni va kursga tegishliligini saqlaydi; uning `Option`
qatorlari tanlovlardir. `single_choice` savolda variantlar ichida aynan bittasi
`is_correct=true` bo‘lishi shart. `essay` savolda option umuman yuborilmaydi;
teacherning private `marking_note`i manual baholash mezoni sifatida saqlanadi.
Bu ichki bank: pupil uchun read yoki write endpoint yo‘q, shuning uchun correct
flag va marking note pupil javobiga hech qachon chiqmaydi.

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `GET /api/v1/courses/<course_id>/questions/` | Faqat shu kursga bugun faol biriktirilgan teacher | `200` — draft/published savollar va private teacher maydonlari | Pupil `403`; boshqa teacher yoki course `404` |
| `POST /api/v1/questions/` | Faol biriktirilgan teacher; `course`, `stem`, `type`; single-choice uchun `options`, essay uchun optional `marking_note` | `201` — `version=1`, `state=draft` savol | 0 yoki 2+ correct option, essayga `options`, ortiqcha field `400`; begona course `404`; pupil `403` |
| `GET /api/v1/questions/<id>/` | Savol kursiga faol biriktirilgan teacher | `200` — faol draft/published savol | Boshqa teacher yoki archived ID `404`; pupil `403` |
| `PATCH /api/v1/questions/<id>/` | Faol biriktirilgan teacher | `200` — draft o‘rnida yangilanadi; published savol o‘rniga yangi draft version qaytadi | Essayga `options`, noto‘g‘ri correct option `400`; begona teacher `404` |
| `POST /api/v1/questions/<id>/archive/` | Faol biriktirilgan teacher; body kerak emas | `200` — `state=archived`, `archived_at` bilan qator saqlanadi | Boshqa teacher/pupil `404`/`403` |

```bash
curl -X POST http://127.0.0.1:8000/api/v1/questions/ \
  -H "Authorization: Bearer <access-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "course": 12,
    "stem": "2 + 2 nechiga teng?",
    "options": [
      {"text": "3", "is_correct": false},
      {"text": "4", "is_correct": true}
    ]
  }'
```

Published savol PATCH qilinganda eski qator va uning option kaliti o‘zgarmaydi:
yangi qator o‘sha `family_id`, keyingi `version`, `state=draft` bilan yaratiladi.
`(family_id, version)` PostgreSQL unique constraint bilan takrorlanmaydi.
Question version hali examga ulanmagan; exam, grading, random tanlash va AI bu
bosqichda yo‘q.

### Draft nazorat metadata’si

`opens_at` va `closes_at` nazorat oynasini, `duration` bitta attempt uchun
vaqt limitini, `attempt_limit` urinishlar sonini belgilaydi. `result_release_at`
natijani chiqarish siyosatining chegarasidir: kelajakdagi pupil result endpointi
faqat nazorat yopilgach **va** bu vaqt yetgach score qaytarishi mumkin. Hozir
score, javob kaliti, savol tarkibi yoki pupil attempt endpointi umuman yo‘q.

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `GET /api/v1/exams/` | Teacher — faol biriktirilgan course’dagi draft/published metadata; pupil — faqat kelajakda published bo‘ladigan, o‘z course’idagi metadata | `200` — metadata ro‘yxati, score/kalitsiz | Pupil draftlarni bo‘sh ro‘yxatda ko‘radi |
| `POST /api/v1/exams/` | Faqat faol assigned teacher; course, term, title, opens/closes, duration, attempt_limit, result_release_at | `201` — `state=draft` Exam metadata | Begona course `404`; teskari vaqt, 0 duration/limit, course-term yil nomosligi `400`; pupil `403` |
| `GET /api/v1/exams/<id>/` | Teacher o‘z scope’ida; pupil faqat published resource bo‘lganda | `200` — metadata | Draft/begona ID `404` |
| `PUT/PATCH /api/v1/exams/<id>/` | Faqat faol assigned teacher, draft metadata maydonlari | `200` — yangilangan metadata | Noto‘g‘ri vaqt/limit `400`; pupil `403` |

```bash
curl -X POST http://127.0.0.1:8000/api/v1/exams/ \
  -H "Authorization: Bearer <access-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "course": 12,
    "term": 3,
    "title": "Kasrlar nazorati",
    "opens_at": "2026-11-25T04:00:00Z",
    "closes_at": "2026-11-25T06:00:00Z",
    "duration": "01:00:00",
    "attempt_limit": 1,
    "result_release_at": "2026-11-25T06:00:00Z"
  }'
```

### Draft nazorat savollari

`ExamItem` hozir question bankdagi aniq `question` ID va `question_version`
ga reference qiladi; bu publish snapshot emas. `position` savol tartibini,
`points` esa uning max ballini belgilaydi. `PUT` ro‘yxatni to‘liq atomar
almashtiradi: ro‘yxatdagi bitta item yaroqsiz bo‘lsa, avvalgi itemlar saqlanadi.

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `PUT /api/v1/exams/<id>/items/` | Faqat faol assigned teacher va draft Exam; body — itemlar JSON ro‘yxati | `200` — position bo‘yicha tartiblangan yangi itemlar | Begona/archived question yoki noto‘g‘ri version, 0 points, duplicate position `400`; published/begona exam `404`; pupil `403` |

```bash
curl -X PUT http://127.0.0.1:8000/api/v1/exams/8/items/ \
  -H "Authorization: Bearer <access-token>" \
  -H "Content-Type: application/json" \
  -d '[
    {"question": 31, "question_version": 1, "points": "2.50", "position": 0},
    {"question": 35, "question_version": 2, "points": "5.00", "position": 1}
  ]'
```

Random selection, publish, snapshot, attempt yoki answer endpointi bu bosqichda
yo‘q; P065 publish vaqtida immutable snapshotni yaratadi.

### Exam publish va immutable snapshot

`POST /publish/` draft Exam itemlarini va ularning savol matni, turi, essay
`marking_note`, option matni, correct kaliti, points va positionini serverda
snapshot qiladi. Shundan keyin bank savolini tahrirlash published exam tarkibini
o‘zgartirmaydi. Publish bitta transaction va exam row lock bilan bajariladi;
takror so‘rov dastlabki publish vaqtini saqlaydi va yangi audit/snapshot qatori
yaratmaydi.

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `POST /api/v1/exams/<id>/publish/` | Faqat faol assigned teacher; body kerak emas | `200` — `state=published`, `published_at` bilan metadata | Items bo‘sh, key/version/course, duration/limit/release vaqt yaroqsiz `400`; begona exam `404`; pupil `403` |

Publish audit actor, vaqt va umumiy max ballni saqlaydi. Snapshotdagi kalit va
marking note hech qaysi pupil responsega chiqmaydi; attempt/start/answer/check
endpointlari hali yo‘q.

### Kalitsiz pupil nazorat kartasi

Pupil endpointlari “hidden” private fieldga tayanmaydi: response serializerda
`is_correct`, `marking_note`, score, item yoki snapshot maydoni mutlaqo yo‘q.
Pupil published examning `upcoming`, `open` yoki `closed` holatini ko‘radi;
draft va boshqa course exam IDlari `404`. Teacher uchun alohida preview endpoint
published snapshotning private key/note maydonlarini qaytaradi.

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `GET /api/v1/exams/` | Pupil — o‘z course’idagi published examlar | `200` — `id`, title, opens/closes, duration, attempt_limit, status | Draft boshqa userga chiqmaydi |
| `GET /api/v1/exams/<id>/` | Pupil — o‘z published exam’i | `200` — ayni kalitsiz karta | Draft yoki begona exam `404` |
| `GET /api/v1/exams/<id>/preview/` | Faqat faol assigned teacher; published exam | `200` — snapshot itemlari, `marking_note`, optionlar va `is_correct` | Pupil `403`; begona/draft exam `404` |

Pupil cardda status: `upcoming` (`opens_at`dan oldin), `open` (oyna ichida),
`closed` (`closes_at`dan keyin).

### Pupil urinishini boshlash

`POST /api/v1/exams/<id>/attempts/` faqat current pupil uchun ishlaydi. Exam
`published` va ayni paytda ochiq bo‘lishi, pupilning shu course bo‘yicha faol
Enrollment’i hamda urinish limiti yaroqli bo‘lishi shart. Bir pupil-exam jufti
uchun faol urinish atigi bitta: enrollment qatori row lock ostida tekshiriladi
va parallel startlar aynan o‘sha mavjud yoki yangi attemptni qaytaradi.

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `POST /api/v1/exams/<id>/attempts/` | Faqat faol enrollment’li pupil; body yo‘q | `201` — `id`, `attempt_number`, `started_at`, `expires_at`, `state`, `server_now`, tartiblangan item/option textlari | Draft/yopiq exam, inactive enrollment yoki limit `400`; begona exam `404`; teacher `403` |

`expires_at = min(started_at + duration, closes_at)`. Startda published exam
snapshotidan savol va option tartibi `AttemptItem`/`AttemptOption`ga bir marta
ko‘chiriladi. Pupil payloadida `is_correct`, marking note, points, score yoki
boshqa baholash kaliti umuman mavjud emas.

### Attemptni qayta ochish va versionli autosave

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `GET /api/v1/attempts/<id>/` | Faqat urinish egasi bo‘lgan pupil | `200` — kalitsiz savol/optionlar, oldingi answer va uning `version`/`saved_at` ACK’i | Begona ID `404`; teacher `403` |
| `PUT /api/v1/attempts/<id>/answers/<item_id>/` | Faqat urinish egasi bo‘lgan pupil | `200` — server saqlagan `selected_option` yoki `essay_text`, yangi `version`, `saved_at` | Begona item/attempt `404`; stale version, expired yoki inactive attempt, noto‘g‘ri option/tur `400`; teacher `403` |

Single-choice body faqat `{ "selected_option": <shu item option ID>,
"expected_version": <n> }`; essay body faqat `{ "essay_text": "...",
"expected_version": <n> }`. Birinchi saqlash `expected_version=0` bilan
boshlanadi. Keyingi saqlashda qiymat joriy server versioniga teng bo‘lishi
kerak; aks holda eski tabning lost update’i rad etiladi. Har PUT transaction va
row lock ichida `active` attempt hamda `now < expires_at` shartlarini qayta
tekshiradi. Finish yoki scoring endpointi hali yo‘q.

### Attemptni yakunlash

`POST /api/v1/attempts/<id>/finish/` body qabul qilmaydi. U faqat avval serverda
saqlangan single-choice answerlarni immutable publish snapshot kaliti bilan
hisoblaydi. Essay item bor bo‘lsa objective qism hisoblangan bo‘lsa ham status
`pending_manual`; essaysiz urinish `finalized`. Finish expiry’dan keyin ham
ishlaydi, biroq yangi answer qabul qilmaydi.

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `POST /api/v1/attempts/<id>/finish/` | Faqat urinish egasi bo‘lgan pupil; body yo‘q | `200` — `status` (`finalized` yoki `pending_manual`) va `finished_at` | Begona attempt `404`; body yuborish yoki yaroqsiz holat `400`; teacher `403` |

Attempt row lock ostida bir marta yakunlanadi; takroriy POST avvalgi yakunni
qaytaradi. `objective_score` serverda saqlanadi, ammo P063 result-release
siyosati tufayli pupil finish responseida score yoki kalit chiqmaydi. Finish,
essay grading, natijani release qilish yoki expiry worker qo‘shmaydi.

### Essay baholash va release qilingan natija

Objective score finishda, manual score esa faqat active `can_grade` teacher
tomonidan server snapshot itemining `max_score` chegarasida qo‘shiladi. Har
essay grade `expected_version` bilan yangilanadi va immutable audit yozuvini
oladi. Barcha essay itemlar baholanmaguncha AttemptResult `pending_manual`,
`total_score` esa `null`; yakunda server `objective + manual` totalini hisoblab
`finalized` qiladi.

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `GET /api/v1/attempts/<id>/grading/` | Faqat course bo‘yicha active `can_grade` teacher | `200` — essay text, max score, current grade va audit | Begona/no-grade teacher `404`; pupil `403` |
| `PUT /api/v1/attempts/<id>/items/<item_id>/grade/` | Shu `can_grade` teacher | `200` — points, feedback, version, graded_at | Non-essay/begona item `404`; stale version yoki 0..max_score tashqarisi `400` |
| `GET /api/v1/attempts/<id>/result/` | Faqat attempt egasi pupil | `200` — status va finalized bo‘lsa total/max total | Begona ID yoki exam yopilmagan/release vaqt kelmagan `404` |
| `GET /api/v1/me/exam-gradebook/` | Pupil | `200` — release qilingan finalized exam natijalari, `source_kind=exam` | Pupil bo‘lmagan user `403` |
| `GET /api/v1/courses/<course_pk>/exam-gradebook/` | Active `can_grade` teacher | `200` — course attempt natijalari, `source_kind=exam` | Begona/no-grade teacher `404`; pupil `403` |

Pupil natijasi faqat `closes_at` hamda `result_release_at` o‘tgach ko‘rinadi;
pending manual holatda total chiqmaydi. Gradebook qatori `AttemptResult`dan
o‘qiladi, shuning uchun duplicate finish yoki manual grade update qo‘shimcha
jurnal yozuvi yaratmaydi. Chorak weight, AI auto-grade va appeal yo‘q.

### Expired attemptni serverda yakunlash

Frontend timer deadline manbai emas: `expires_at` serverdagi yagona chegara.
Answer PUT `now >= expires_at` bo‘lsa har doim rad etiladi — Celery worker yoki
Beat ishlamayotgani bu qoidani o‘zgartirmaydi. Pupil expired attemptni GET qilsa
server shu safe finalize xizmatini chaqirib, mavjud javoblarni yakunlaydi.

Celery Beat har 60 soniyada `assessments.tasks.finalize_expired_attempts`
taskini default queue’ga yuboradi. Task expiry bo‘yicha tartiblangan ko‘pi bilan
100 active attemptni tanlaydi, transient DB xatolarida exponential retry qiladi.
U `finalize_attempt`ning row lock/idempotent qoidalarini qayta ishlatadi:
API finish va worker bir paytda ishlasa ham bitta AttemptResult yaratiladi;
kechikkan scheduler deadline’ni uzaytirmaydi.

### Attempt lifecycle regression transcript (P072)

Schema contract `python manage.py spectacular --validate --file /tmp/p072-schema.yml`
bilan tekshiriladi. Quyidagi curl oqimi pupil tokeni bilan API lifecycle’ni
takrorlaydi; `selected_option` va `expected_version` oldingi response’dan
olinadi.

```bash
curl -X POST "$API/exams/$EXAM_ID/attempts/" -H "Authorization: Bearer $TOKEN"
curl "$API/attempts/$ATTEMPT_ID/" -H "Authorization: Bearer $TOKEN"
curl -X PUT "$API/attempts/$ATTEMPT_ID/answers/$ITEM_ID/" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"selected_option": 42, "expected_version": 0}'
curl -X POST "$API/attempts/$ATTEMPT_ID/finish/" -H "Authorization: Bearer $TOKEN"
curl "$API/attempts/$ATTEMPT_ID/result/" -H "Authorization: Bearer $TOKEN"
```

Ikkinchi start mavjud active attemptni, ikkinchi finish mavjud final resultni
qaytaradi. Stale version va `expires_at`dan keyingi answer `400`; release
vaqtigacha result `404`. Pupil payloadlari recursive tekshiruvda `is_correct`
va `marking_note`dan xoli. Real concurrency regressiyasi alohida PostgreSQL
connectionlari bilan start, finish hamda worker/API expiry race’ini tekshiradi.

### Private fayl yuklash

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `POST /api/v1/attachments/` | ******; `multipart/form-data` ichida bitta `file` maydoni; PDF/JPEG/PNG, 10 MiB gacha | `201` — `id`, UUID `key`, `original_name`, `size`, `detected_mime`, `scan_status=pending_scan` | MIME/kengaytma noto‘g‘ri yoki `owner`/`key`/`scan_status` kabi ortiqcha maydon `400`; 10 MiB dan katta fayl `413`; token yo‘q `401` |
| `GET /api/v1/attachments/<id>/download/` | ******; egasi yoki o‘z role/course scope’idagi published topic ichidagi published PDF resursga ruxsati bor foydalanuvchi; attachment `clean` bo‘lishi shart | `200` — `Content-Disposition: attachment` bilan fayl oqimi | Yashirin/begona yoki mavjud bo‘lmagan fayl, pending/rejected scan `404`; token yo‘q `401` |

Server MIME turini bytes’dan `python-magic`/`libmagic` orqali aniqlab, fayl
kengaytmasi bilan solishtiradi; client yuborgan `Content-Type`ga ishonmaydi.
Bytes private storage’da UUID key bilan saqlanadi, metadata javobida public URL
bo‘lmaydi. Muvaffaqiyatli upload `pending_scan` holatida qoladi.
`GET /api/v1/attachments/<id>/download/` clean faylni egasiga yoki ruxsatli,
published resurs ota-onasi orqali foydalanuvchiga beradi; pending/rejected fayl
hatto egasiga ham berilmaydi. Lokal adapter storage API’dan bytes’ni ochib,
`FileResponse` orqali uzatadi, public media URL yaratmaydi. Keyinchalik S3
adapteri shu storage interfeysida ishlashi mumkin; uzoq fayllar uchun parent
ruxsati tekshirilgandan keyingina qisqa muddatli signed URL’ga o‘tish staging
bosqichida ko‘riladi. Hozir signed URL yoki submission parent yo‘q. Runtime
muhitda `python-magic`dan tashqari tizimning `libmagic` kutubxonasi ham kerak.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/attachments/ \
  -H "Authorization: Bearer <access-token>" \
  -F "file=@lesson.pdf;type=application/pdf"
```

Javob:

```json
{
  "id": 1,
  "key": "4d20c3fd-cd9a-4af6-8847-252f2d198ce6",
  "original_name": "lesson.pdf",
  "size": 32768,
  "detected_mime": "application/pdf",
  "scan_status": "pending_scan"
}
```

## Swagger’da sinash

## Pupil dashboard

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `GET /api/v1/me/dashboard/` | Bearer JWT; faqat pupil | `200` — bugungi darslar, 3 ta yaqin deadline, completion va so‘nggi published feedback | token yo‘q `401`; teacher `403` |

Dashboard aggregation resource: u yangi baho yoki completion yozmaydi, mavjud
course scope’dan o‘qiydi. `completion.eligible_topics` maxraji faqat joriy
pupilning faol course’laridagi published va arxivlanmagan topiclardan iborat;
`completed_topics` esa o‘sha topiclardagi `read` completion soni. Bu mastery
ham, baho ham emas. Deadline ichidagi `completion_status` mavzu read holati,
`submission_status` esa topshirish/tekshirish holati bo‘lib, ular alohida
maydonlardir. Draft yoki ungraded grade feedback ro‘yxatiga kirmaydi va hech
qachon `0` ballga aylantirilmaydi.

```bash
curl http://127.0.0.1:8000/api/v1/me/dashboard/ \
  -H "Authorization: Bearer <access-token>"
```

```json
{
  "today_lessons": [{"id": 17, "course_id": 4, "course": "Matematika", "starts_at": "2026-11-25T09:00:00Z", "ends_at": "2026-11-25T10:00:00Z", "room": "101"}],
  "completion": {"eligible_topics": 8, "completed_topics": 5},
  "upcoming_deadlines": [{"assignment_id": 12, "topic_id": 9, "course_id": 4, "title": "Kasrlar", "due_at": "2026-11-27T12:00:00Z", "completion_status": "completed", "submission_status": "submitted_ungraded"}],
  "recent_feedback": [{"assignment_id": 11, "title": "Tenglamalar", "course_id": 4, "total": "8.00", "feedback": "Yaxshi", "published_at": "2026-11-24T08:00:00Z"}]
}
```

## Teacher dashboard

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `GET /api/v1/teaching/dashboard/` | Bearer JWT; faqat teacher | `200` — tegishli bugungi sessionlar hamda tekshirish countlari | token yo‘q `401`; pupil `403` |

Bu aggregation yangi baho yozmaydi. `today_sessions` faqat teacherning faol
biriktirilgan course’ida va teacher o‘zi (yoki override sifatida) o‘tadigan,
bekor qilinmagan bugungi sessionlarni qaytaradi. `review_summary` esa faqat
`can_grade=True` bo‘lgan course’lardagi eng so‘nggi revisionlarni hisoblaydi:
`submitted_ungraded_count` — return qilinmagan va hali grade yozilmagan ish;
`returned_count` — return qilingan ish; draft yoki published grade bu ikki
countga kirmaydi. Shuning uchun haqiqiy `0.00` baho “ungraded” hisoblanmaydi.

`late_queue` mavjud assignment-scoped queue’ga navigation contract beradi:
`endpoint_template`dagi `{assignment_id}` o‘rniga teacher ko‘ra oladigan
assignment IDsi qo‘yiladi, `query` esa `late=true&status=submitted` tarzida
yuboriladi. Shu filterlar `GET /api/v1/assignments/<id>/submissions/` va
`GET /api/v1/courses/<id>/gradebook/`da ham ishlaydi; `status` qiymatlari
`submitted`, `draft`, `graded`, `returned`. Queue faqat minimal pupil
ID/ism/familiyasini beradi, dashboardning o‘zi pupil profilini bermaydi.

```bash
curl http://127.0.0.1:8000/api/v1/teaching/dashboard/ \
  -H "Authorization: Bearer <access-token>"
```

```json
{
  "today_sessions": [{"id": 17, "course_id": 4, "course": "Matematika", "starts_at": "2026-11-25T09:00:00Z", "ends_at": "2026-11-25T10:00:00Z", "room": "101"}],
  "review_summary": {
    "submitted_ungraded_count": 4,
    "returned_count": 1,
    "late_submitted_ungraded_count": 2,
    "late_queue": {
      "endpoint_template": "/api/v1/assignments/{assignment_id}/submissions/",
      "query": {"late": "true", "status": "submitted"},
      "description": "Teacher-scoped assignment uchun kech, baholanmagan navbat."
    }
  }
}
```

1. Serverni ishga tushirib `/api/docs/`ni oching.
2. `POST /api/v1/auth/login/`ga username/parolni yuboring; CSRF talab qilinmaydi.
3. Javobdagi access tokenni Swagger **Authorize** oynasida Bearer token sifatida
   kiriting. `POST /api/v1/accounts/` va barcha boshqa API’lar shu JWT’dan foydalanadi.
4. Logout’da access tokenni Authorize’da qoldirib, request body’ga
   `{"refresh":"<refresh-token>"}` yuboring.

`GET /api/docs/` `200` qaytarishi lokal serverda tekshirildi. Qo‘shimcha
qo‘llanish misollari `docs/SWAGGER_GUIDE.md` va boshlang‘ich ko‘rsatmalar
`docs/USER_GUIDE.md`da.
