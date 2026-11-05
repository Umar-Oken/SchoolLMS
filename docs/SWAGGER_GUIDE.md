# School LMS API: Swagger qo‘llanmasi

## Swagger UI

Development server ishga tushgach, brauzerda `http://localhost:8000/api/docs/`
ni oching. OpenAPI schema `http://localhost:8000/api/schema/`, ReDoc esa
`http://localhost:8000/api/redoc/` manzilida. Ushbu sahifalar faqat
`DEBUG=True` bo‘lganda yoqiladi. Endpointlar, metodlar, request/response
maydonlari va permissionlar uchun `docs/API_GUIDE.md`ga qarang.

## JWT bilan ishlash

API JWT Bearer token ishlatadi. Swagger’da browser-native HTTP Basic sign-in
oynasi chiqmasligi kerak; autentifikatsiya faqat Swagger’ning **Authorize**
oynasi orqali amalga oshiriladi. Login, logout, token refresh va account create
uchun CSRF token talab qilinmaydi. Django admin/session hamda
`CsrfViewMiddleware` himoyasi saqlanadi.

1. `POST /api/v1/auth/login/`ni ochib, username va parolni JSON body’da yuboring:

   ```json
   {"username": "teacher01", "password": "<parol>"}
   ```

2. `200` javobdan `access` va `refresh` tokenlarni oling.
3. **Authorize** oynasiga access tokenni kiriting. Swagger `Bearer` prefiksini
   qo‘shib, himoyalangan API so‘rovlariga yuboradi.
4. `POST /api/v1/accounts/` ochiq registration endpointidir: JWT, login yoki
   CSRF token talab qilmaydi. `username`, `first_name`, `last_name`, `role`
   (`pupil` yoki `teacher`) va `password` majburiy; `preferred_language`
   ixtiyoriy. Parol response’da qaytmaydi.
5. Logout uchun protected `POST /api/v1/auth/logout/`ni `{"refresh":"<refresh>"}`
   body bilan chaqiring. Logout parol so‘ramaydi; refresh token blacklist qilinadi.
6. Access token muddati tugaganda `POST /api/v1/auth/token/refresh/`ga
   `{"refresh":"<refresh>"}` yuboring. Logout qilingan refresh token `401` oladi.

Noto‘g‘ri credential yoki yaroqsiz token `401`, permission yetishmasa `403`,
noto‘g‘ri body `400` qaytaradi. `GET /api/v1/auth/csrf/` legacy yordamchi route
bo‘lib qolgan; JWT API uchun undan foydalanish shart emas.

## Himoyalangan endpointlarda curl

```bash
BASE=http://localhost:8000
ACCESS_TOKEN=<access-token>
curl -H "Authorization: Bearer $ACCESS_TOKEN" "$BASE/api/v1/me/"
```

Token va parollarni URL, shell history, chat yoki repository’ga joylamang.
