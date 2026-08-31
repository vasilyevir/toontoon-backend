# 29 — Маппинг на стандарты (OWASP / CWE / ASVS)

> Сопоставление файлов библиотеки со стандартами. Помогает: (1) убедиться в полноте покрытия,
> (2) ссылаться на стандарты в отчётах, (3) находить нужный чеклист по типу уязвимости.

**Легенда:** 🔴 Critical · 🟠 High · 🟡 Medium · 🟢 Low · ⚪ Info

---

## 1. OWASP Top 10 (2021) → файлы

| OWASP | Название | Файлы библиотеки |
|-------|----------|------------------|
| **A01** | Broken Access Control | [02](./02-authorization-idor.md), [03](./03-session-management.md), [08](./08-csrf-cors-headers.md), [17](./17-business-logic.md) |
| **A02** | Cryptographic Failures | [04](./04-secrets-management.md), [14](./14-cryptography-signatures.md), [24](./24-data-privacy-compliance.md) |
| **A03** | Injection | [05](./05-injections.md), [06](./06-input-validation.md), [07](./07-xss.md) |
| **A04** | Insecure Design | [13](./13-payments-webhooks.md), [16](./16-referral-commission.md), [17](./17-business-logic.md) |
| **A05** | Security Misconfiguration | [08](./08-csrf-cors-headers.md), [10](./10-frontend-config.md), [19](./19-infrastructure-docker-k8s.md), [20](./20-cloud-security.md), [23](./23-error-handling.md) |
| **A06** | Vulnerable & Outdated Components | [21](./21-dependency-supply-chain.md) |
| **A07** | Identification & Auth Failures | [01](./01-authentication.md), [03](./03-session-management.md) |
| **A08** | Software & Data Integrity Failures | [05](./05-injections.md) (deserialization), [13](./13-payments-webhooks.md), [21](./21-dependency-supply-chain.md) |
| **A09** | Security Logging & Monitoring Failures | [22](./22-logging-monitoring.md), [25](./25-incident-response.md) |
| **A10** | Server-Side Request Forgery (SSRF) | [11](./11-ssrf.md) |

---

## 2. OWASP API Security Top 10 (2023) → файлы

| API | Название | Файлы |
|-----|----------|-------|
| **API1** | Broken Object Level Authorization (BOLA/IDOR) | [02](./02-authorization-idor.md) |
| **API2** | Broken Authentication | [01](./01-authentication.md) |
| **API3** | Broken Object Property Level Authorization | [02](./02-authorization-idor.md) |
| **API4** | Unrestricted Resource Consumption | [09](./09-api-security.md) |
| **API5** | Broken Function Level Authorization | [02](./02-authorization-idor.md) |
| **API6** | Unrestricted Access to Sensitive Business Flows | [17](./17-business-logic.md) |
| **API7** | Server Side Request Forgery | [11](./11-ssrf.md) |
| **API8** | Security Misconfiguration | [08](./08-csrf-cors-headers.md), [19](./19-infrastructure-docker-k8s.md) |
| **API9** | Improper Inventory Management | [09](./09-api-security.md) |
| **API10** | Unsafe Consumption of APIs | [11](./11-ssrf.md), [21](./21-dependency-supply-chain.md) |

---

## 3. OWASP ASVS 4.0 → файлы

| ASVS | Раздел | Файлы |
|------|--------|-------|
| V2 | Authentication | [01](./01-authentication.md) |
| V3 | Session Management | [03](./03-session-management.md) |
| V4 | Access Control | [02](./02-authorization-idor.md) |
| V5 | Validation, Sanitization, Encoding | [05](./05-injections.md), [06](./06-input-validation.md), [07](./07-xss.md) |
| V6 | Stored Cryptography | [14](./14-cryptography-signatures.md), [04](./04-secrets-management.md) |
| V7 | Error Handling & Logging | [22](./22-logging-monitoring.md), [23](./23-error-handling.md) |
| V8 | Data Protection | [24](./24-data-privacy-compliance.md) |
| V9 | Communication | [08](./08-csrf-cors-headers.md), [19](./19-infrastructure-docker-k8s.md) |
| V10 | Malicious Code | [05](./05-injections.md), [21](./21-dependency-supply-chain.md) |
| V11 | Business Logic | [13](./13-payments-webhooks.md), [16](./16-referral-commission.md), [17](./17-business-logic.md) |
| V12 | Files and Resources | [12](./12-file-upload.md), [11](./11-ssrf.md) |
| V13 | API and Web Service | [09](./09-api-security.md), [13](./13-payments-webhooks.md) |
| V14 | Configuration | [08](./08-csrf-cors-headers.md), [19](./19-infrastructure-docker-k8s.md), [20](./20-cloud-security.md), [21](./21-dependency-supply-chain.md) |

---

## 4. Ключевые CWE → файлы

| CWE | Название | Файл |
|-----|----------|------|
| CWE-20 | Improper Input Validation | [06](./06-input-validation.md) |
| CWE-22 | Path Traversal | [12](./12-file-upload.md) |
| CWE-78 | OS Command Injection | [05](./05-injections.md) |
| CWE-79 | XSS | [07](./07-xss.md) |
| CWE-89 | SQL Injection | [05](./05-injections.md) |
| CWE-200 | Information Exposure | [04](./04-secrets-management.md), [23](./23-error-handling.md) |
| CWE-209 | Error Message Info Exposure | [23](./23-error-handling.md) |
| CWE-250 | Execution with Unnecessary Privileges | [18](./18-database-orm.md), [19](./19-infrastructure-docker-k8s.md) |
| CWE-284 | Improper Access Control | [02](./02-authorization-idor.md), [19](./19-infrastructure-docker-k8s.md) |
| CWE-287 | Improper Authentication | [01](./01-authentication.md) |
| CWE-306 | Missing Authentication | [19](./19-infrastructure-docker-k8s.md) |
| CWE-307 | Improper Restriction of Auth Attempts | [01](./01-authentication.md), [09](./09-api-security.md) |
| CWE-312 | Cleartext Storage | [04](./04-secrets-management.md), [14](./14-cryptography-signatures.md) |
| CWE-321 | Hard-coded Crypto Key | [14](./14-cryptography-signatures.md) |
| CWE-345 | Insufficient Verification of Data Authenticity | [13](./13-payments-webhooks.md) |
| CWE-347 | Improper Verification of Cryptographic Signature | [01](./01-authentication.md), [14](./14-cryptography-signatures.md) |
| CWE-352 | CSRF | [08](./08-csrf-cors-headers.md) |
| CWE-362 | Race Condition | [13](./13-payments-webhooks.md), [17](./17-business-logic.md), [18](./18-database-orm.md) |
| CWE-434 | Unrestricted File Upload | [12](./12-file-upload.md) |
| CWE-502 | Deserialization of Untrusted Data | [05](./05-injections.md) |
| CWE-522 | Insufficiently Protected Credentials | [04](./04-secrets-management.md) |
| CWE-532 | Info in Log Files | [22](./22-logging-monitoring.md) |
| CWE-611 | XXE | [05](./05-injections.md) |
| CWE-639 | IDOR | [02](./02-authorization-idor.md) |
| CWE-760 | Predictable Salt | [14](./14-cryptography-signatures.md) |
| CWE-798 | Hard-coded Credentials | [04](./04-secrets-management.md) |
| CWE-840 | Business Logic Errors | [16](./16-referral-commission.md), [17](./17-business-logic.md) |
| CWE-918 | SSRF | [11](./11-ssrf.md) |
| CWE-942 | Permissive CORS | [08](./08-csrf-cors-headers.md) |
| CWE-1188 | Insecure Default | [04](./04-secrets-management.md), [19](./19-infrastructure-docker-k8s.md) |

---

## 5. Полнота покрытия (self-check)

| Домен | Покрыт файлами | ✔ |
|-------|----------------|---|
| Идентификация и доступ | 01, 02, 03 | ✅ |
| Секреты и крипто | 04, 14 | ✅ |
| Инъекции и валидация | 05, 06, 07 | ✅ |
| API и конфигурация | 08, 09, 10 | ✅ |
| SSRF и файлы | 11, 12 | ✅ |
| Деньги/крипта/Web3 | 13, 14, 15, 16 | ✅ |
| Бизнес-логика и данные | 17, 18, 24 | ✅ |
| Инфраструктура | 19, 20, 21 | ✅ |
| Наблюдаемость и ошибки | 22, 23 | ✅ |
| Процессы | 00, 25, 26, 27, 28 | ✅ |

---

## 6. Внешние ссылки

- OWASP Top 10 (2021): https://owasp.org/Top10/
- OWASP API Security Top 10 (2023): https://owasp.org/API-Security/
- OWASP ASVS 4.0: https://owasp.org/www-project-application-security-verification-standard/
- OWASP MASVS (mobile): https://mas.owasp.org/
- OWASP Cheat Sheet Series: https://cheatsheetseries.owasp.org/
- CWE: https://cwe.mitre.org/
- CIS Benchmarks: https://www.cisecurity.org/cis-benchmarks
- SWC Registry (smart contracts): https://swcregistry.io/
- NIST SP 800-61 (Incident Handling): https://csrc.nist.gov/publications/detail/sp/800-61/rev-2/final
