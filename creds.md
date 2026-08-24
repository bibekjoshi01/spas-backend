# Demo sign-ins

Seeded into the **demo** college. Open **http://demo.localhost:3000** and sign in.

Every account below uses the password `Password@123`.

| Role | Username | Sees |
|---|---|---|
| Principal | `principal` | superuser — the whole college |
| Department head | `bikash.rana` | CSIT — that department only |
| Teacher | `ram.gurung` | CSIT — own classes only |
| Teacher | `sita.adhikari` | CSIT — own classes only |
| Programme coordinator | `sarita.koirala` | BSCCSIT — that programme only |
| Department head | `nabin.shrestha` | MGMT — that department only |
| Teacher | `hari.poudel` | MGMT — own classes only |
| Programme coordinator | `pooja.thapa` | BBA — that programme only |

## What each one proves

- **Principal** — a superuser. Every department, every programme, every account. Their *My Classes* is empty because nothing is allocated to them, which is the point: allocation, not rank, decides whose classes those are.
- **Department head** — manages one department: its programmes, teachers, curriculum and students. Another department's records return 404, not a filtered-empty list.
- **Programme coordinator** — manages one programme's batches, curriculum, allocations and students. Cannot create programmes.
- **Teacher** — no management screens at all. Their sidebar is the workspace, and it shows only the classes allocated to them.

Regenerate with:

```bash
python manage.py seed_demo_data demo
```
