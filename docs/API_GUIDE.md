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
- Autentifikatsiyasiz so‘rov `401`, permission rad etilganda `403`; noto‘g‘ri
  JSON yoki biznes validatsiyasi `400`;
  mavjud bo‘lmagan yoki foydalanuvchi scope’idan tashqari obyekt `404`;
  yopiq HTTP metodi `405` qaytaradi.
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
yaratishi va faqat o‘zining scope’dagi draftini ko‘rishi mumkin. Pupil bu
bosqichda assignment API’dan foydalana olmaydi. `state` server tomonidan
`draft` qilib o‘rnatiladi; `teacher`, `published_at` va rubric maydonlari input
emas.

| Metod va yo‘l | Kirish / ruxsat | Muvaffaqiyatli javob | Asosiy xatolar |
|---|---|---|---|
| `POST /api/v1/assignments/` | ****** + faol assignment’li teacher; `topic`, `title`, `instructions`, `available_at`, `due_at`, `closes_at`, `max_score`, `max_attempts` | `201` — yaratilgan assignment, `state:"draft"` | Pupil `403`; begona/arxivlangan topic `404`; noto‘g‘ri sana yoki ortiqcha maydon `400` |
| `GET /api/v1/assignments/<id>/` | ****** + faol biriktirilgan teacher; faqat o‘z drafti | `200` — assignment metadata | Pupil `403`; boshqa teacher, published vazifa yoki topilmagan ID `404` |

Datetime UTC-aware ISO 8601 formatida yuboriladi; `available_at <= due_at <=
closes_at` bo‘lishi shart. `max_score` musbat Decimal, `max_attempts` esa
1–32767 oralig‘idagi butun son. Response `id`, `topic`, asosiy maydonlar va
server belgilagan `state`ni qaytaradi; rubric bu endpointda yaratilmaydi.

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
```

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

1. Serverni ishga tushirib `/api/docs/`ni oching.
2. `POST /api/v1/auth/login/`ga username/parolni yuboring; CSRF talab qilinmaydi.
3. Javobdagi access tokenni Swagger **Authorize** oynasida Bearer token sifatida
   kiriting. `POST /api/v1/accounts/` va barcha boshqa API’lar shu JWT’dan foydalanadi.
4. Logout’da access tokenni Authorize’da qoldirib, request body’ga
   `{"refresh":"<refresh-token>"}` yuboring.

`GET /api/docs/` `200` qaytarishi lokal serverda tekshirildi. Qo‘shimcha
qo‘llanish misollari `docs/SWAGGER_GUIDE.md` va boshlang‘ich ko‘rsatmalar
`docs/USER_GUIDE.md`da.
