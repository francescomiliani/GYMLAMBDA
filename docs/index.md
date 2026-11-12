# docs/ - knowledge base index

Router principale. Segui la progressive disclosure: identifica la categoria
pertinente, apri il suo indice, poi solo i documenti rilevanti al task.
Un fatto vive in un solo posto; le regole universali stanno in `AGENTS.md`.

## Categorie

| Categoria | Cosa contiene | Indice |
|---|---|---|
| `architecture/` | Risorse AWS, componenti del bot Telegram, confini di sicurezza | `docs/architecture/index.md` |
| `reference/` | Costi AWS, quote gratuite, tabelle di configurazione | `docs/reference/index.md` |
| `how-to/` | Procedure: build, deploy, rollback, configurazione del bot | `docs/how-to/index.md` |
| `decisions/` | ADR delle decisioni durature | `docs/decisions/index.md` |
| `superpowers/plans/` | Piani di implementazione. Descrivono un'intenzione, non lo stato corrente: verifica nel codice | (nessun indice) |

## Convenzioni

- Ogni documento sotto `architecture/`, `reference/`, `how-to/`, `decisions/`
  inizia con frontmatter `title`, `type`, `status`, `owner`, `last-verified`.
- `status: canonical` prevale su `draft`, `deprecated`, `superseded`. A parita'
  di stato, architecture prevale su plans per descrivere il comportamento corrente.
- Ogni documento nuovo va elencato nell'indice della sua categoria.
- Mai il carattere em-dash (U+2014): usa il trattino.
- I valori AWS (quote, prezzi, stato account) sono uno snapshot datato:
  controlla `last-verified` e rivalida prima di decidere sulla spesa.
