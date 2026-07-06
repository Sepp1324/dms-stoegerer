# Secrets & Zugangsdaten

Grundsatz: **Kein Secret in Git** — nicht in Code, Config, Commits, Issues oder
Kommentaren. Alle Geheimnisse liegen ausschließlich im jeweiligen Secret-Store.
`deploy/k8s/secret.yaml` ist gitignored (siehe `.gitignore`).

Übersicht, welches Geheimnis wo lebt:

| Geheimnis | Zweck | Ablage |
|---|---|---|
| App-Secrets (Django `SECRET_KEY`, DB-Passwort, KI-API-Keys) | Laufzeit im Cluster | k8s-Secret im Namespace `dms` (`deploy/k8s/secret.yaml`, gitignored) |
| Registry-Login | `docker push` im CI | `~/.docker/config.json` des Runner-Nutzers (`docker login`, einmalig) |
| Deploy nach `main` | Checkout im Workflow | automatischer `GITHUB_TOKEN` des Runners — **kein** PAT nötig |
| **GitHub-Push-Token (PAT)** | Agenten pushen Branches & öffnen PRs | **k8s-Secret** (Agent-Runtime) **oder** GitHub-Actions-Secret — siehe unten |

## Stabiles `POSTGRES_PASSWORD`

`POSTGRES_PASSWORD` ist kein normal rotierbares Deploy-Secret. Das offizielle
Postgres-Image setzt das Passwort nur beim ersten `initdb` auf leerem
`postgres-data` PVC. Danach lebt das echte DB-Passwort in der Datenbank. Wenn ein
Kustomize-`secretGenerator` oder ein anderes Deploy-Werkzeug später ein neues
`POSTGRES_PASSWORD` erzeugt, zeigen Backend, Worker und Backup auf den neuen
Secret-Wert, während Postgres noch das alte Passwort erwartet. Ergebnis:
`password authentication failed for user "dms"`.

Regeln:
- `dms-secrets` wird als stabiler Secret-Name geführt, nicht generiert.
- `POSTGRES_PASSWORD` bleibt ein fester Wert in `deploy/k8s/secret.yaml`
  (gitignored) oder in einem echten SealedSecret.
- Kein `secretGenerator` unter `deploy/k8s`.
- Passwortwechsel nur kontrolliert: erst DB-User per `ALTER USER` ändern, dann
  denselben Wert im Secret aktualisieren und Backend/Worker neu starten.

Stabilen Wert einmalig setzen/ersetzen:

```bash
kubectl -n dms create secret generic dms-secrets \
  --from-literal=DJANGO_SECRET_KEY='<stabiler-django-secret-key>' \
  --from-literal=POSTGRES_PASSWORD='<stabiler-db-wert>' \
  --from-literal=ANTHROPIC_API_KEY='' \
  --from-literal=OPENAI_API_KEY='' \
  --from-literal=IMAP_PASSWORD='' \
  --dry-run=client -o yaml | kubectl apply -f -
```

Repository-Guard:

```bash
scripts/verify-stable-secrets.sh
```

Dieser Check läuft in PR-Checks und im Deploy-Gate. Er schlägt fehl, sobald unter
`deploy/k8s` wieder ein `secretGenerator` auftaucht.

### Optional: SealedSecret statt gitignored Secret

Wenn das Passwort versioniert im Repo liegen soll, dann nicht als Klartext-Secret,
sondern als SealedSecret, verschlüsselt für den Cluster:

```bash
kubectl -n dms create secret generic dms-secrets \
  --from-literal=DJANGO_SECRET_KEY='<stabiler-django-secret-key>' \
  --from-literal=POSTGRES_PASSWORD='<stabiler-db-wert>' \
  --from-literal=ANTHROPIC_API_KEY='' \
  --from-literal=OPENAI_API_KEY='' \
  --from-literal=IMAP_PASSWORD='' \
  --dry-run=client -o yaml \
  | kubeseal --format yaml > deploy/k8s/sealed-secret.yaml
```

`sealed-secret.yaml` darf nur committed werden, wenn der Sealed-Secrets-Controller
im Cluster installiert ist und der erzeugte Wert wirklich für diesen Cluster
verschlüsselt wurde.

## GitHub-Push-Token (PAT) für Agenten

Der automatische `GITHUB_TOKEN` gilt nur *innerhalb* eines Actions-Laufs. Damit
Agenten (oder ein Mensch von außerhalb des Runners) **Branches pushen und PRs
öffnen** können, braucht es ein separates Credential. Empfohlen: ein
**Fine-grained Personal Access Token**, ausgestellt nur auf das Repo
`Sepp1324/dms-stoegerer`, mit minimalen Rechten:

- **Contents:** Read and write (Branches pushen)
- **Pull requests:** Read and write (PRs öffnen)
- Ablaufdatum setzen (z. B. 90 Tage), danach rotieren.

### Variante A — k8s-Secret (Agent-Runtime)

Für Agenten, die im/aus dem Cluster arbeiten. Secret **imperativ** anlegen
(landet nicht in Git):

```bash
kubectl -n dms create secret generic git-push-credentials \
  --from-literal=GITHUB_TOKEN='<NEUER_PAT>'
# Rotation: bestehendes Secret ersetzen
kubectl -n dms create secret generic git-push-credentials \
  --from-literal=GITHUB_TOKEN='<NEUER_PAT>' \
  --dry-run=client -o yaml | kubectl apply -f -
```

Der Token wird als Env-Var in die Agent-Umgebung gereicht und **nur zur Laufzeit**
in den Git-Credential-Helper geschrieben (siehe unten). Er wird **nicht** in eine
getrackte Datei geschrieben.

### Variante B — GitHub-Actions-Secret

Falls Push/PR aus einem Workflow nötig wird: Repo → **Settings → Secrets and
variables → Actions → New repository secret**, Name z. B. `GH_PUSH_TOKEN`. Im
Workflow via `${{ secrets.GH_PUSH_TOKEN }}` referenzieren — nie echoen/loggen.

## Git-Credential zur Laufzeit setzen (ohne Persistenz in Git)

Token kommt aus der Env (k8s-Secret), nicht aus einer Datei im Repo:

```bash
# origin steht bereits auf https://github.com/Sepp1324/dms-stoegerer
git config --global credential.helper store        # nur, wenn ein sicherer $HOME genutzt wird
# Bevorzugt: kurzlebig via Env, ohne Schreiben auf Platte:
git -c credential.helper='!f() { echo "username=x-access-token"; echo "password=$GITHUB_TOKEN"; }; f' \
    push -u origin <branch>
```

Alternativ für `gh`:

```bash
echo "$GITHUB_TOKEN" | gh auth login --with-token
gh pr create --base main --fill
```

## Rotation

1. Neuen Fine-grained PAT in GitHub erzeugen (minimale Rechte, s. o.).
2. k8s-Secret bzw. Actions-Secret **ersetzen** (Kommandos oben).
3. Alten Token in GitHub **widerrufen** (Settings → Developer settings → Tokens).
4. Rotation kurz vermerken (Datum, wer) — **ohne** den Tokenwert.

> ⚠️ Ein einmal in Klartext (Issue/Kommentar/Commit) aufgetauchter Token gilt als
> **kompromittiert** und muss sofort widerrufen und neu ausgestellt werden.
> Ein neuer Token wird ausschließlich über den Secret-Store bereitgestellt.
