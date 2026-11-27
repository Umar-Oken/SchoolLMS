# Nginx reverse proxy va TLS (P081-A)

Nginx public HTTPS edge, Gunicorn esa faqat `127.0.0.1:8000`dagi private WSGI
processdir. [Nginx namunasi](../deploy/nginx/schoollms.conf.example) faqat
`/api/`ni Gunicornga uzatadi; frontend/static sahifa bermaydi. U `Host`, client
IP, `X-Forwarded-For`, `X-Forwarded-Host` va `X-Forwarded-Proto`ni yuboradi.

Deployda `schoollms.conf.example`ni Nginx `http` contextida include qilinadigan
`conf.d`ga, `schoollms-proxy.conf`ni esa `/etc/nginx/snippets/`ga nusxalang.
`conf.d` fayli `limit_req_zone`ni `http` contextida e'lon qilishi kerak; uni
`server` bloki ichiga ko'chirmang.
Shuning uchun sertifikat bilan birga process environmentda quyidagilar yoqiladi:

```sh
DJANGO_ALLOWED_HOSTS=api.example.edu
DJANGO_CSRF_TRUSTED_ORIGINS=https://api.example.edu
DJANGO_TRUST_PROXY_SSL_HEADER=true
DJANGO_SECURE_SSL_REDIRECT=true
DJANGO_SECURE_HSTS_SECONDS=31536000
```

`DJANGO_TRUST_PROXY_SSL_HEADER=true` faqat Gunicorn public tarmoqdan
tinglamaganda va Nginx o'zi boshqargan `X-Forwarded-Proto`ni yuborganda xavfsiz.
Oldinda load balancer bo'lsa, `real_ip_header`/`set_real_ip_from`ga faqat uning
aniq IP yoki CIDRini kiriting; aks holda login IP limiter spoof qilinishi mumkin.

`check --deploy` HSTS preloadni ham eslatadi. Preload barcha subdomainlar HTTPS
va uzoq muddatli HSTS talab qilgani uchun bu deploymentda yoqilmaydi; domen
egasi shu talablarni tasdiqlamaguncha warning qasddan ochiq qoladi.

## Edge chegaralari

`/api/v1/auth/login/` uchun Nginx IP limit 10 request/minute, Django esa
username+IP bo'yicha 5/minute cheklov qo'llaydi. Bu ikki cheklov bir-birini
to'ldiradi, permission yoki biznes kvotasi emas. Nginx 429 va 413 javoblarini
`code`, `message`, `fields`, `request_id` JSON envelope shaklida qaytaradi.

`client_max_body_size 11m` multipart metadata uchun joy qoldiradi; faylning
qat'iy 10 MiB limiti Django upload handlerida saqlanadi. Shuning uchun Nginx
bodyni erta rad etsa ham, Django file limit rad etsa ham frontend bir xil
`file_too_large` contractini oladi.

`/private_files/` va `/media/` explicit 404. Attachment faqat authorization
qilingan Django download endpointi orqali beriladi; Nginxga private katalog uchun
`alias`, `root` yoki `try_files` qo'shmang.

## TLS deploy va smoke

Bu repositoryda Nginx binary ham, `api.example.edu` sertifikati ham yo'q;
shu sabab lokal `nginx -t` va HTTPS smoke bajarilmagan. Deploy operatori
`server_name`, certificate/key yo'llari va Gunicorn bindini haqiqiy qiymatlar
bilan almashtirgach quyidagini bajaradi:

```sh
nginx -t
systemctl reload nginx
curl --fail --resolve api.example.edu:443:PUBLIC_IP \
  https://api.example.edu/api/v1/auth/csrf/ -c /tmp/schoollms-cookies.txt
curl --fail --resolve api.example.edu:443:PUBLIC_IP \
  https://api.example.edu/api/v1/auth/login/ \
  -H 'Content-Type: application/json' \
  --data '{"username":"review-user","password":"review-password"}'
```

Birinchi javobda `Secure` CSRF cookie va JSON token bo'lishi kerak; ikkinchisi
valid review hisobida JWT token qaytaradi. Login endpoint JWT flow bo'lgani
uchun CSRF token talab qilmaydi, lekin Django admin/session POSTlari proxy orqali
`https://api.example.edu` origin va CSRF cookie bilan himoyalanganicha qoladi.
Global CSRF middleware o'chirilmaydi.
