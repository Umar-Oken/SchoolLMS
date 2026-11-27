# AI savol qoralamasi request API (P086)

`POST /api/v1/ai/question-drafts/` faqat faol biriktirilgan teacherning clean,
published PDF resource'i uchun ishlaydi. Body resource ID va uning joriy
`resource_version`idan iborat; server doim **5** question draft request qiladi.
`Idempotency-Key` majburiy. Javob `202 Accepted` bo'lsa ham savollar tayyor
emas — u faqat atomik tarzda queued job va durable outbox signal yozilganini
bildiradi.

```json
{
  "job_id": 41,
  "status": "queued",
  "status_url": "https://api.example.edu/api/v1/ai/jobs/41/",
  "request_id": "..."
}
```

`GET /api/v1/ai/jobs/{id}/` faqat uni yaratgan teacherga ochiq. Boshqa teacher,
pupil yoki begona job `404` oladi. `queued`/`running` holatida natija hali
mavjud emas; `review`dagi qoralama ham baho emas va bu promptda approve action
yo'q.

## Kvota va retry

Kunlik business quota defaultda teacher uchun 10 job (`AI_DAILY_JOB_LIMIT`).
U PostgreSQL `AIDailyQuota` qatorida row lock bilan reservation sifatida
oshiriladi; AIJob va `ai.question_draft.requested` outbox event shu transaction
ichida yaratiladi. Shu sabab rollbackda quota ham saqlanmaydi.

Bir xil actor + `Idempotency-Key` + resource/version retry avvalgi jobni
`202` bilan qaytaradi, yangi job, outbox event yoki quota reservation yaratmaydi.
Shu key boshqa resource/version bilan qayta ishlatilsa `409` qaytadi. Bu DRF
throttle emas: throttle abuse'ni taxminan kamaytiradi, daily quota esa aniq
biznes limiti.

## Failure refund siyosati

Reservation queued/running/review davomida saqlanadi. Provider ish boshlaganidan
keyingi `failed` job avtomatik refund qilinmaydi: external ish allaqachon xarajat
qilgan bo'lishi mumkin va retry limiti chetlab o'tilmasligi kerak. Hali worker
scope'i yo'q; keyingi worker/operator scope'i faqat provider **ishga
jo'natilmaganini** isbotlagan preflight failure uchun bir martalik, auditli
refund qo'shishi mumkin. Hozir user retry uchun yangi key ishlatadi va quota
qolgan bo'lsa yangi queued job oladi.
