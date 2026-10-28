# School LMS API: Swagger qo‘llanmasi

## 1. Swagger nima?

Swagger — API manzillari, ularning kiritish maydonlari va javoblarini ko‘rsatadigan
interaktiv hujjat. Swagger UI orqali so‘rov yuborib, javobni brauzerda ko‘rish mumkin.
Bu qo‘llanmadagi namunalar sinov uchun; maxfiy parol va vaqtinchalik parollarni
ommaviy joyga yubormang.

## 2. Swagger UI va autentifikatsiya

Lokal server ishga tushgach, brauzerda `http://localhost:8000/api/docs/` ni oching.
OpenAPI JSON `http://localhost:8000/api/schema/`, Redoc esa
`http://localhost:8000/api/redoc/` manzilida. Bu hujjat sahifalari faqat
`DEBUG=True` bo‘lganda yoqiladi.

Loyihada JWT yoki Bearer token emas, Django **session cookie** ishlatiladi. Swagger
UI’dagi **Authorize** oynasida Bearer token kiritmang. Brauzerda ayni host uchun
session cookie bo‘lsa, UI so‘rovlarni shu cookie bilan yuboradi. Avval
`GET /api/v1/auth/csrf/` ni **Try it out → Execute** qiling; javobdagi `csrfToken`
qiymatini yozib oling. Keyingi `POST` va `PATCH` so‘rovlarida **X-CSRFToken**
header maydoniga shu qiymatni kiriting. So‘ng `/api/v1/auth/login/` orqali
username/parol bilan kiring; muvaffaqiyatli javob brauzerda session cookie
o‘rnatadi. Login muvaffaqiyatsiz bo‘lsa `400`, CSRF tekshiruvi o‘tmasa `403`.

Har bir operatsiya uchun: tegishli app bo‘limidan endpointni toping, **Try it out**
ni bosing, path/query/body/header maydonlarini to‘ldiring va **Execute** ni bosing.
Quyidagi `curl` misollarida `BASE`, `cookies` va `CSRF_TOKEN` oldindan sozlangan,
foydalanuvchi esa ko‘rsatilgan rol bilan tizimga kirgan deb olinadi:

```bash
BASE=http://localhost:8000
CSRF_TOKEN=$(curl -s -c cookies "$BASE/api/v1/auth/csrf/" \
  | python -c 'import json,sys; print(json.load(sys.stdin)["csrfToken"])')
```

`401` OpenAPI’da umumiy autentifikatsiya xatosi sifatida ko‘rsatiladi. Joriy
`SessionAuthentication` sozlamasida session yo‘q so‘rov odatda `403` qaytaradi;
CSRF xatosi ham `403` bo‘ladi. Scope’dan tashqaridagi yoki mavjud bo‘lmagan
resurslar `404` bilan yashiriladi.

## 3. Endpointlar

### Accounts — autentifikatsiya

#### `GET /api/v1/auth/csrf/` — CSRF token olish

- **Nima uchun:** himoyalangan yozish so‘rovlariga kerak bo‘ladigan CSRF cookie va tokenni olish.
- **Swagger UI:** Auth → endpoint → **Try it out** → **Execute**. Body va token kerak emas.
- **Kirish maydonlari:** yo‘q.
- **Autentifikatsiya:** login talab qilinmaydi.
- **Muvaffaqiyatli javob (`200`):** `{"csrfToken":"csrf-token-namuna"}`.
- **Xato javob (`403`):** `{"detail":"CSRF tekshiruvi rad etdi."}`.
- **curl:**

```bash
curl -i -c cookies "$BASE/api/v1/auth/csrf/"
```

#### `POST /api/v1/auth/login/` — tizimga kirish

- **Nima uchun:** username va parolni tekshirib session ochish.
- **Swagger UI:** avval CSRF endpointini chaqiring; **Try it out** ichida `username`,
  `password` va `X-CSRFToken` ni kiriting; **Execute** ni bosing.
- **Kirish maydonlari:** `username` (majburiy, masalan `teacher01`);
  `password` (majburiy, masalan `Kuchli-parol-123`). Header:
  `X-CSRFToken` (majburiy, CSRF endpointi bergan qiymat).
- **Autentifikatsiya:** endpoint uchun oldindan login shart emas; javob session cookie beradi.
- **Muvaffaqiyatli javob (`200`):** `{"detail":"Muvaffaqiyatli tizimga kirildi."}`.
- **Xato javob (`400`):** `{"detail":"Login yoki parol noto‘g‘ri."}`.
- **Xato javob (`403`):** `{"detail":"CSRF token missing or incorrect."}`.
- **curl:**

```bash
curl -i -b cookies -c cookies -H "Content-Type: application/json" \
  -H "X-CSRFToken: $CSRF_TOKEN" \
  -d '{"username":"teacher01","password":"Kuchli-parol-123"}' \
  "$BASE/api/v1/auth/login/"
```

#### `POST /api/v1/auth/logout/` — tizimdan chiqish

- **Nima uchun:** joriy sessionni tugatish.
- **Swagger UI:** login bo‘ling, CSRF tokenni oling, headerni kiriting va **Execute** ni bosing.
- **Kirish maydonlari:** body yo‘q; `X-CSRFToken` headeri majburiy.
- **Autentifikatsiya:** session kerak.
- **Muvaffaqiyatli javob (`200`):** `{"detail":"Tizimdan chiqildi."}`.
- **Xato javob (`403`):** `{"detail":"You do not have permission to perform this action."}`.
- **curl:**

```bash
curl -i -b cookies -X POST -H "X-CSRFToken: $CSRF_TOKEN" \
  "$BASE/api/v1/auth/logout/"
```

### Accounts — profil va parol

#### `GET /api/v1/me/` — profilni ko‘rish

- **Nima uchun:** login qilingan foydalanuvchining ID, username, ism, familiya, role va tilini olish.
- **Swagger UI:** login bo‘ling va endpointda **Try it out → Execute** ni bosing.
- **Kirish maydonlari:** yo‘q.
- **Autentifikatsiya:** session kerak.
- **Muvaffaqiyatli javob (`200`):**
  `{"id":7,"username":"teacher01","first_name":"Ali","last_name":"Valiyev","role":"teacher","preferred_language":"uz"}`.
- **Xato javob (`403`):** `{"detail":"Authentication credentials were not provided."}`.
- **curl:**

```bash
curl -i -b cookies "$BASE/api/v1/me/"
```

#### `PATCH /api/v1/me/` — profilni qisman yangilash

- **Nima uchun:** ism, familiya yoki afzal tilni o‘zgartirish; yuborilmagan maydonlar o‘zgarishsiz qoladi.
- **Swagger UI:** `PATCH` operatsiyasini tanlang, o‘zgartiriladigan maydonlarni JSON’da
  kiriting va CSRF headerni to‘ldirib **Execute** ni bosing.
- **Kirish maydonlari:** `first_name` (ixtiyoriy, masalan `Ali`), `last_name`
  (ixtiyoriy, masalan `Valiyev`), `preferred_language` (ixtiyoriy, masalan `uz`).
  `username`, `role` va noma’lum maydonlar o‘zgartirilmaydi.
- **Autentifikatsiya:** session va `X-CSRFToken` kerak.
- **Muvaffaqiyatli javob (`200`):** profilning to‘liq JSON ko‘rinishi.
- **Xato javob (`400`):** `{"role":["Bu maydonni o‘zgartirib bo‘lmaydi."]}`.
- **Xato javob (`403`):** session yoki CSRF xatosi.
- **curl:**

```bash
curl -i -b cookies -X PATCH -H "Content-Type: application/json" \
  -H "X-CSRFToken: $CSRF_TOKEN" \
  -d '{"first_name":"Ali","preferred_language":"uz"}' "$BASE/api/v1/me/"
```

#### `POST /api/v1/me/change-password/` — parolni almashtirish

- **Nima uchun:** joriy parolni tekshirib, kuchli yangi parol o‘rnatish.
- **Swagger UI:** ikki parol maydonini to‘ldiring, CSRF header kiriting va **Execute** ni bosing.
- **Kirish maydonlari:** `current_password` (majburiy, joriy parol);
  `new_password` (majburiy, Django parol qoidalariga mos yangi parol).
- **Autentifikatsiya:** session va `X-CSRFToken` kerak.
- **Muvaffaqiyatli javob (`200`):** `{"detail":"Parol muvaffaqiyatli o‘zgartirildi."}`.
- **Xato javob (`400`):** `{"current_password":["Joriy parol noto‘g‘ri."]}` yoki yangi parol validatsiyasi xatosi.
- **curl:**

```bash
curl -i -b cookies -H "Content-Type: application/json" \
  -H "X-CSRFToken: $CSRF_TOKEN" \
  -d '{"current_password":"Eski-parol-1","new_password":"Yangi-kuchli-parol-2026"}' \
  "$BASE/api/v1/me/change-password/"
```

### Accounts — account boshqaruvi

#### `POST /api/v1/accounts/` — pupil yoki teacher account yaratish

- **Nima uchun:** `manage_accounts` ruxsati bor foydalanuvchi account ochadi; server bir martalik vaqtinchalik parol beradi.
- **Swagger UI:** login bo‘lingan operator sifatida body maydonlarini va CSRF headerni to‘ldirib **Execute** qiling.
- **Kirish maydonlari:** `username` (majburiy, masalan `pupil07`), `role`
  (majburiy, `pupil` yoki `teacher`); `first_name`, `last_name`,
  `preferred_language` ixtiyoriy.
- **Autentifikatsiya:** `manage_accounts` ruxsatli session va CSRF kerak.
- **Muvaffaqiyatli javob (`201`):**
  `{"id":18,"username":"pupil07","first_name":"Aziza","last_name":"Karimova","role":"pupil","preferred_language":"uz","temporary_password":"bir-martalik-parol"}`.
- **Xato javob (`400`):** maydon validatsiyasi yoki username takrorlanganida:
  `{"username":["A user with that username already exists."]}`.
- **Xato javob (`403`):** ruxsat yetarli emas.
- **curl:**

```bash
curl -i -b cookies -H "Content-Type: application/json" \
  -H "X-CSRFToken: $CSRF_TOKEN" \
  -d '{"username":"pupil07","first_name":"Aziza","last_name":"Karimova","role":"pupil","preferred_language":"uz"}' \
  "$BASE/api/v1/accounts/"
```

#### `POST /api/v1/accounts/{id}/reset-password/` — parolni qayta o‘rnatish

- **Nima uchun:** ruxsatli operator account uchun yangi vaqtinchalik parol chiqaradi.
- **Swagger UI:** `pk` ga account ID kiriting, body’ni `{}` qoldiring, CSRF headerni to‘ldiring va **Execute** qiling.
- **Kirish maydonlari:** `pk` (majburiy path ID, masalan `18`); bo‘sh JSON body `{}`; `X-CSRFToken` majburiy.
- **Autentifikatsiya:** `manage_accounts` ruxsatli session kerak.
- **Muvaffaqiyatli javob (`200`):** account ma’lumotlari va yangi `temporary_password`.
- **Xato javob (`404`):** `{"detail":"Foydalanuvchi topilmadi."}`.
- **Xato javob (`400`):** staff/superuser account yoki noto‘g‘ri body.
- **Xato javob (`403`):** ruxsat yetarli emas.
- **curl:**

```bash
curl -i -b cookies -H "Content-Type: application/json" \
  -H "X-CSRFToken: $CSRF_TOKEN" -d '{}' \
  "$BASE/api/v1/accounts/18/reset-password/"
```

### Academics — kurslar

#### `GET /api/v1/courses/` — kurslar ro‘yxati

- **Nima uchun:** teacher o‘ziga biriktirilgan, pupil esa qatnashadigan arxivlanmagan kurslarni ko‘rish.
- **Swagger UI:** login bo‘ling va **Try it out → Execute** ni bosing; body yo‘q.
- **Kirish maydonlari:** yo‘q.
- **Autentifikatsiya:** session kerak; ro‘llar bo‘yicha scope serverda tekshiriladi.
- **Muvaffaqiyatli javob (`200`):**
  `[{"id":12,"subject":{"code":"MATH","title":"Matematika"},"school_class":{"grade_level":7,"section":"A"},"teacher_display":"Ali Valiyev","year":{"label":"2025-2026"}}]`.
- **Xato javob (`403`):** login/session yoki rol xatosi.
- **curl:**

```bash
curl -i -b cookies "$BASE/api/v1/courses/"
```

#### `GET /api/v1/courses/{id}/` — kurs tafsiloti

- **Nima uchun:** o‘zingiz qatnashadigan yoki dars beradigan bitta kursni ko‘rish.
- **Swagger UI:** `pk` ga kurs ID kiriting va **Execute** ni bosing.
- **Kirish maydonlari:** `pk` (majburiy integer path, masalan `12`).
- **Autentifikatsiya:** session kerak.
- **Muvaffaqiyatli javob (`200`):** kurs ro‘yxatidagi bitta obyekt.
- **Xato javob (`404`):** `{"detail":"Not found."}` — kurs yo‘q yoki sizga ko‘rinmaydi.
- **curl:**

```bash
curl -i -b cookies "$BASE/api/v1/courses/12/"
```

### Learning — mavzular

Topic — kurs ichidagi dars/mavzu. Teacher o‘z mavzularining draft va published
holatini ko‘radi; pupil faqat o‘z kursidagi published mavzularni ko‘radi.
Arxivlash qatorni o‘chirmaydi, shuning uchun eski baho va ishlar tarixi
saqlanadi; ularni mavzuga bog‘lovchi FK aloqalari buzilmaydi.

#### `GET /api/v1/courses/{course_pk}/topics/` — kurs mavzulari

- **Nima uchun:** ko‘rsatilgan kursdagi foydalanuvchiga ruxsatli mavzularni olish.
- **Swagger UI:** `course_pk` ga kurs ID kiriting, **Execute** ni bosing.
- **Kirish maydonlari:** `course_pk` (majburiy integer path, masalan `12`).
- **Autentifikatsiya:** session kerak; pupilga faqat published mavzular ko‘rinadi.
- **Muvaffaqiyatli javob (`200`):**
  `[{"id":42,"course":12,"term":3,"title":"Kasrlar","objective":"Oddiy kasrlarni taqqoslash","order":1,"state":"published","published_at":"2026-03-19T10:00:00Z","archived_at":null}]`.
- **Xato javob (`404`):** kurs yo‘q yoki foydalanuvchi unga tegishli emas.
- **curl:**

```bash
curl -i -b cookies "$BASE/api/v1/courses/12/topics/"
```

#### `POST /api/v1/topics/` — draft mavzu yaratish

- **Nima uchun:** biriktirilgan kurs uchun yangi draft ochish. Yaratishda mavzu nashr etilmaydi.
- **Swagger UI:** body’dagi `course`, `term`, `title`, `objective`, `order` ni kiriting;
  CSRF headerni to‘ldirib **Execute** qiling.
- **Kirish maydonlari:** `course` (majburiy kurs ID), `term` (majburiy term ID;
  course yili bilan bir xil bo‘lishi shart), `title` (majburiy, 200 belgigacha),
  `objective` (majburiy), `order` (majburiy musbat tartib raqami).
  `state`, `published_at` va `teacher` yuborilmaydi.
- **Autentifikatsiya:** active assignment’li teacher sessioni va CSRF kerak.
- **Muvaffaqiyatli javob (`201`):** yaratilgan Topic; `state` serverda `draft`,
  `published_at` esa `null` bo‘ladi.
- **Xato javob (`400`):** majburiy maydon, term yili yoki unikal `(course, term, order)` xatosi.
- **Xato javob (`403`/`404`):** pupil yozishi `403`; begona course `404`.
- **curl:**

```bash
curl -i -b cookies -H "Content-Type: application/json" \
  -H "X-CSRFToken: $CSRF_TOKEN" \
  -d '{"course":12,"term":3,"title":"Kasrlar","objective":"Oddiy kasrlarni taqqoslash","order":1}' \
  "$BASE/api/v1/topics/"
```

#### `GET /api/v1/topics/{id}/` — mavzu tafsiloti

- **Nima uchun:** bitta ruxsatli mavzuning ma’lumotini ko‘rish.
- **Swagger UI:** `pk` ga mavzu ID kiriting va **Execute** ni bosing.
- **Kirish maydonlari:** `pk` (majburiy integer path, masalan `42`).
- **Autentifikatsiya:** session kerak.
- **Muvaffaqiyatli javob (`200`):** mavzuning `id`, `course`, `term`, `title`,
  `objective`, `order`, `state`, `published_at`, `archived_at` maydonlari.
- **Xato javob (`404`):** mavjud bo‘lmagan, scope’dan tashqaridagi yoki pupil uchun draft mavzu.
- **curl:**

```bash
curl -i -b cookies "$BASE/api/v1/topics/42/"
```

#### `PATCH /api/v1/topics/{id}/` — draftni tahrirlash

- **Nima uchun:** faqat draft mavzuning ayrim maydonlarini almashtirish.
- **Swagger UI:** `pk` kiriting, o‘zgartiriladigan maydonlarnigina JSON’da yuboring,
  CSRF headerni to‘ldiring va **Execute** qiling.
- **Kirish maydonlari:** `term` (ixtiyoriy ID, kurs yiliga mos), `title`,
  `objective`, `order`. `course`, `state` va `published_at` o‘zgartirilmaydi.
- **Autentifikatsiya:** mavzuga biriktirilgan active teacher sessioni va CSRF kerak.
- **Muvaffaqiyatli javob (`200`):** yangilangan mavzu.
- **Xato javob (`400`):** noto‘g‘ri term yoki ruxsat etilmagan maydon.
- **Xato javob (`403`/`404`):** CSRF/ruxsat xatosi yoki draft bo‘lmagan/begona mavzu.
- **curl:**

```bash
curl -i -b cookies -X PATCH -H "Content-Type: application/json" \
  -H "X-CSRFToken: $CSRF_TOKEN" \
  -d '{"title":"Kasrlarni taqqoslash"}' "$BASE/api/v1/topics/42/"
```

#### `POST /api/v1/topics/{id}/archive/` — mavzuni arxivlash

- **Nima uchun:** mavzuni faol ro‘yxatdan yashirish, lekin qator va unga bog‘liq tarixni saqlab qolish.
- **Swagger UI:** `pk` kiriting, body yubormang, CSRF headerni to‘ldirib **Execute** qiling.
- **Kirish maydonlari:** `pk` (majburiy mavzu ID); body yo‘q.
- **Autentifikatsiya:** biriktirilgan teacher sessioni va CSRF kerak.
- **Muvaffaqiyatli javob (`200`):** mavzu obyektida `archived_at` vaqt bilan to‘ldiriladi.
- **Xato javob (`403`/`404`):** ruxsat yo‘q yoki mavzu topilmadi.
- **curl:**

```bash
curl -i -b cookies -X POST -H "X-CSRFToken: $CSRF_TOKEN" \
  "$BASE/api/v1/topics/42/archive/"
```

#### `POST /api/v1/topics/{id}/publish/` — mavzuni nashr qilish

- **Nima uchun:** to‘ldirilgan draftni pupil ko‘ra oladigan published mavzuga aylantirish.
- **Swagger UI:** `pk` kiriting, body yubormang, CSRF headerni kiriting va **Execute** qiling.
- **Kirish maydonlari:** `pk` (majburiy mavzu ID); body yo‘q. Title va objective bo‘sh bo‘lmasin,
  term course yiliga mos bo‘lsin.
- **Autentifikatsiya:** biriktirilgan teacher sessioni va CSRF kerak.
- **Muvaffaqiyatli javob (`200`):** `state` `published`, `published_at` esa nashr vaqtiga teng.
  Takroriy chaqiruv ikkinchi mavzu yaratmaydi va dastlabki vaqtni saqlaydi.
- **Xato javob (`400`):** title/objective bo‘sh yoki term yili mos emas.
- **Xato javob (`403`/`404`):** pupil yoki begona teacher rad etiladi; ko‘rinmaydigan mavzu topilmagandek qaytadi.
- **curl:**

```bash
curl -i -b cookies -X POST -H "X-CSRFToken: $CSRF_TOKEN" \
  "$BASE/api/v1/topics/42/publish/"
```

## 4. Tez-tez uchraydigan xatolar

| Xato | Sabab va yechim |
|---|---|
| `403 Forbidden` | Session yo‘q, rol/ruxsat mos emas yoki CSRF header noto‘g‘ri. Avval login qiling va CSRF tokenni qayta oling. |
| `401 Unauthorized` | Joriy app session auth ishlatadi; token kiritish talab qilinmaydi. Proxy yoki boshqa auth qatlami 401 qaytarsa, login/sessionni tekshiring. |
| CSRF xatosi | CSRF cookie bilan `X-CSRFToken` qiymati bir juft bo‘lishi kerak. Cookie o‘zgarsa tokenni qayta oling. |
| `400 Bad Request` | Body JSON noto‘g‘ri, majburiy maydon yo‘q yoki biznes qoidasi buzilgan. Javobdagi maydon xabarini o‘qing. |
| `404 Not Found` | ID mavjud emas yoki resurs sizning scope’ingizga kirmaydi. Kurs/mavzu ID va foydalanuvchi rolini tekshiring. |
| Swagger sahifasi ochilmaydi | `/api/docs/` faqat `DEBUG=True` muhitida yoqilgan. Lokal server sozlamalarini tekshiring. |
| Swagger’da session ishlamaydi | Docs va API bir xil host/origin’da bo‘lishi, brauzer cookie’larni bloklamasligi kerak. Qayta login qiling va yozish so‘rovida CSRF headerni to‘ldiring. |
| `405 Method Not Allowed` | Ushbu endpointda HTTP usuli ochilmagan. Masalan Topic uchun PUT va DELETE qo‘llanmaydi; PATCH yoki tegishli actiondan foydalaning. |

Yangi endpoint paydo bo‘lganda uning schema izohi va ushbu qo‘llanmadagi endpoint
bo‘limi ham yangilanadi; mavjud tushuntirishlar o‘chirib yuborilmaydi.
