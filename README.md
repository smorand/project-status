# project-status

Script Python pour scanner et nettoyer les projets git sous `PROJECT_FOLDER`.

## Usage

```bash
python3 project_status.py
# ou directement
./project_status.py
```

## Variables d'environnement

| Variable | Défaut | Description |
|---|---|---|
| `PROJECT_FOLDER` | `$HOME/projects` | Répertoire racine à scanner |
| `PROJECT_FOLDER_MODEL` | `bob/haiku-4.5` | Modèle pi pour générer les messages de commit |
| `GIT_FETCH_TIMEOUT` | `15` | Timeout fetch en secondes |
| `GIT_CMD_TIMEOUT` | `10` | Timeout autres commandes git en secondes |

## Statuts

| Statut | Description |
|---|---|
| `UP_TO_DATE` | Repo propre, synchronisé avec le remote |
| `BACKUPED` | Uncommitted changes sauvegardés sur une branche backup, rebase OK |
| `BACKUP_DIRTY` | Backup créé mais rebase échoué (conflits), intervention manuelle requise |
| `REMOTE_DIVERGENT` | Local et remote ont divergé, fast-forward impossible |
| `MISSING_GIT` | Dossier sans git dont au moins 1/3 des voisins sont sous git |
| `MISSING_REMOTE_UP_TO_DATE` | Repo git sans remote, propre |
| `MISSING_REMOTE_BACKUPED` | Repo git sans remote, backup créé localement |

## Logique de backup

- **Branche protégée** (`main`, `master`, `uat`, `develop`, `tests`, `staging`, `dev`, `production`, `prod`, `preprod`, `release`, `hotfix`) avec uncommitted changes : création d'une branche `backup-YYYYmmdd`, commit, push si remote, puis rebase sur la branche active mise à jour.
- **Branche non protégée** avec uncommitted changes : commit direct sur la branche courante.
- Le message de commit est généré via `pi` si disponible (modèle configuré par `PROJECT_FOLDER_MODEL`), sinon fallback sur `Backup YYYY-mm-dd`.

## Dépendances

- Python 3.10+ (pour `match` et `list[Path]` syntax)
- `git` dans le PATH
- `pi` optionnel (pour les messages de commit intelligents)
