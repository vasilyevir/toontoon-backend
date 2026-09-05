# Toontoon — Privacy Policy

**Last updated:** `[[DATE OF PUBLICATION]]`

> **Draft for counsel.** Written from the code: what leaves our servers, what is
> stored, and what deletion actually does were all checked in `app/`, not taken
> from a template. Everything in `[[DOUBLE BRACKETS]]` has to come from you.
> Three sections marked **⚠** describe things the product does *not* yet do —
> they are deliberately written as they are, not as they should be.

This policy explains what Toontoon does with your photographs and your other data.
It is written to be read, not to be survived, so it is shorter than most — but it
does not leave anything out, including the parts that are not to our credit.

---

## 1. Who is responsible

`[[LEGAL ENTITY, REGISTERED ADDRESS]]` is the controller of your data. Questions
and requests: `[[PRIVACY EMAIL]]`.

## 2. What we collect

| What | Why | Where it is kept |
|---|---|---|
| **Photographs you upload** — selfies, pictures of people or objects, style samples | To produce the picture you asked for | Our object storage |
| **Profiles** — a name you choose and up to 15 of your photographs | So you need not attach a photo every time | Our database and object storage |
| **Results** — the pictures and videos produced for you | Your history in the app | Our object storage |
| **What you type in the chat**, and what we understood from it (the style, the setting, the wardrobe you described) | To carry your answers from one message to the next | Our database |
| **Account** — email address if you sign in with one, or an Apple identifier | To recognise you across devices | Our database |
| **Balance and purchases** — TOONTOON spent and earned, subscription state | Bookkeeping and access to paid features | Our database |
| **Anonymous usage events** — which screens are opened, which steps are abandoned | To find where the app fails people | Our own server, capped log |

Usage events are collected while you use the app; deleting your account removes them along with everything else.
They carry no advertising identifier and are never used to follow you across
other apps or websites, which is why we do not ask for tracking permission.

We do not collect your contacts, your location, your photo library as a whole, or
advertising identifiers. We do not sell data and we do not share it with
advertisers.

## 3. Photographs of faces

Most of what Toontoon holds about you is pictures of your face. That deserves
saying plainly rather than burying in a table.

When you make a picture, your photograph is **sent to an AI provider** so that the
picture can be drawn. When you build a profile, your photographs are also sent to
a provider once, so it can tell us which of them are usable — too dark, face
covered, blurry.

We do not build a face template, a faceprint, or any mathematical model of your
face, and we do not use face recognition to identify you. Your photographs are
stored as pictures.

> **⚠ К юристу.** Здесь должен появиться раздел про биометрию: набор из десяти
> селфи, привязанный к имени, в Иллинойсе (BIPA) и Техасе (CUBI) может считаться
> биометрическими данными независимо от того, строим мы шаблон лица или нет.
> BIPA даёт частный иск с фиксированной суммой за нарушение. Формулировку
> согласия и раздел об удалении должен написать юрист — придумывать их самим
> опаснее, чем оставить этот пробел видимым.

## 4. Who else sees your photographs

| Provider | What we send | What for |
|---|---|---|
| **fal.ai** (fal.ai), which runs the model that draws the picture — currently OpenAI's GPT Image and Google's Nano Banana models on fal's infrastructure | Your photograph and the text instruction | Drawing the picture and editing your earlier results. We ask fal not to keep the request or the result on their side |
| **OpenRouter** (openrouter.ai), a fallback route to the same models when fal is unavailable — not in use at the moment | Your photograph and the text instruction | Drawing the picture |
| **OpenAI** (openai.com) | A small copy of your photograph, and what you type | Understanding your request; checking that the photograph and the request are within our rules (no nudity, no minors in unsuitable settings, no public figures) |
| **Amplitude** (amplitude.com, EU data centre) | What you type in the chat and the text our assistant answers, with e-mail addresses and phone numbers removed before sending; how long the model took and how many tokens it used. Never your photographs | Understanding where the assistant helps and where it fails, so we can improve it |
| **Kie** (kie.ai) | Your photograph and the instruction | Generating video |
| **Apple** | Purchase receipts | Confirming your subscription. Apple takes the payment; we never see your card |

These providers process what we send under their own terms, and they are outside
the EU. We do not control how long they keep it. If that matters to you, their
policies are the place to look.

We also disclose data if the law requires it.

## 5. How long we keep things

> **⚠ Правда, которая неудобна.** Срока хранения нет. Ни у снимков, ни у готовых
> кадров — они лежат, пока их не удалят вручную. Обещать здесь «90 дней» или
> «пока нужно для оказания услуги» нельзя: продукт этого не делает, и написать
> так значило бы соврать в документе, который человек читает перед тем, как
> отдать своё лицо. Срок нужно сперва завести в продукте, потом записать сюда.

Today: if you have an account (an email or an Apple sign-in), your photographs
and results are kept until you delete them or ask us to. If you use the app as
a guest and do not open it for **180 days**, your photographs and results are
deleted automatically; the record that a picture once existed stays, without
the picture.
Bookkeeping records — what was paid and what was spent — are kept for as long as
accounting law requires, whatever else you delete.

## 6. Deleting — what happens, and what does not

Three different actions, with three different effects. We set them out separately
because they are easy to confuse.

- **Deleting one result** — the file is erased from storage and any share link
  stops working. A bookkeeping row survives, because the payment record points at
  it; it holds no picture.
- **Deleting a person from your profiles** — the profile stops being offered.
  **The photographs themselves stay** in your library.
- **Deleting your account** — your email, name and avatar are erased and you are
  signed out everywhere. Your payment history is kept, because accounting requires
  it.

Deletion is started from Settings inside the app, and it does erase the files.
Both were added on 31 August 2026: before that, the row was anonymised and the
photographs stayed — a person pressed delete, was told it was done, and their face
went on being stored.

If you would rather ask us, write to `[[PRIVACY EMAIL]]`.

## 7. Children

Toontoon is not for children under `[[AGE]]`. If we learn that we hold a child's
photographs, we delete them.

## 8. Your rights

Wherever you live, you may ask us for a copy of your data, for a correction, or
for deletion, and we will answer within 30 days. In the EEA and the UK you also
have the right to object to processing, to restrict it, and to complain to your
national supervisory authority. In California you may ask what we collect and ask
us to delete it; we do not sell personal information, so there is nothing to opt
out of.

You do not need an account to ask. Write to `[[PRIVACY EMAIL]]`.

## 9. Security

Traffic between the app and our servers is encrypted. Access to stored photographs
is limited to the people who operate the service. No system is perfect, and we
will tell you without delay if a breach affects your data.

## 10. Changes

If we change something that matters — a new provider that sees your photographs, a
new purpose, a shorter or longer retention — we will tell you in the app or by
email before it takes effect.

## 11. Contact

`[[PRIVACY EMAIL]]` · `[[POSTAL ADDRESS]]` · `[[EU/UK REPRESENTATIVE, IF REQUIRED]]`
