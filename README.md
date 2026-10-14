# Moderations-Bot: Honeypot + Quarantäne

- **Honeypot:** Wer in einen bestimmten Kanal schreibt, landet automatisch in Quarantäne (oder wird gekickt/gebannt). Seine Nachrichten der letzten Minuten werden gelöscht.
- **Quarantäne:** `/quarantine @user` nimmt einem User **alle** Rollen weg (auch Admin). Der Bot merkt sich die Rollen, `/release @user` gibt sie zurück.

---

## 1. Rollen umbauen (in Discord, ohne Bot)

Das Ziel: **Ohne Rollen darf man gar nichts.** Alle normalen Rechte stecken in der Rolle `Member`.

### @everyone leeren
Servereinstellungen → Rollen → `@everyone` → **alle** Berechtigungen ausschalten (vor allem *Kanäle ansehen*, *Nachrichten senden*, *Verbinden*).

### Rolle `Member` anlegen
Folgende Rechte gibst du ihr (Rest aus):
- Kanäle ansehen, Nachrichten senden, In Threads senden, Öffentliche Threads erstellen
- Links einbetten, Dateien anhängen, Reaktionen hinzufügen, Externe Emojis/Sticker verwenden
- Nachrichtenverlauf lesen, App-Befehle verwenden, Nickname ändern
- Verbinden, Sprechen, Video, Sprachaktivität

Dann **allen bestehenden Usern** die Rolle `Member` geben.

### Andere Rollen entschlacken
- Farb-/Spaß-/Info-Rollen: **keine** Berechtigungen.
- Mod: nur die Extras (z.B. Mitglieder timeouten, Nachrichten verwalten).
- Admin: Administrator (oder gezielte Rechte).
- Booster-Rolle: **keine** Berechtigungen. Der Bot kann sie nicht entfernen, weil Discord sie verwaltet.

### Kanal-Berechtigungen prüfen ⚠️ (wichtigster Punkt)
Geh durch **jede Kategorie und jeden Kanal** → Berechtigungen. Wenn dort `@everyone` irgendwo ein grünes ✅ hat, stell es auf `/` (neutral) und gib das ✅ stattdessen `Member`.
Sonst kann ein User ohne Rollen in diesem Kanal trotzdem schreiben.

Ausnahme: Ein Kanal wie `#regeln` darf für `@everyone` lesbar sein, wenn Neue die Regeln sehen sollen.

### Wie bekommen neue User `Member`?
Über den **Regel-Button** des Bots:
1. Kanal `#regeln` anlegen. Berechtigungen: `@everyone` darf *Kanal ansehen* und *Nachrichtenverlauf lesen*, aber **nicht** *Nachrichten senden*. `dc_homepot` darf *Nachrichten senden*.
2. Die Regeln selbst als Nachricht in den Kanal schreiben.
3. In `.env` die `MEMBER_ROLE_ID` eintragen und den Bot neu starten.
4. Im Kanal `/rules-button` ausführen. Optional mit eigenem Text und Button-Beschriftung, z.B. `label: Regeln akzeptieren`.

Wer klickt, bekommt `Member`. User in Quarantäne bekommen sie nicht.

Discords eigenes Onboarding funktioniert hier nicht gut: Es verlangt, dass `@everyone` in mehreren Kanälen schreiben darf.

Zusätzlich unter **Sicherheits-Setup** das Verifizierungslevel auf mindestens *Mittel* stellen. Das hält schon viele Bots fern.

### Rangfolge (Reihenfolge in der Rollenliste)
Man kann nur Rollen entfernen, die **unter** der eigenen höchsten Rolle stehen. Deshalb:

```
Bot-Rolle        ← ganz oben (sonst kann der Bot keine Admins sperren)
Admin
Mod
Quarantäne
Member
…Farbrollen usw.
```

> Admins mit derselben Rolle können sich gegenseitig nicht sperren. Das kann nur der Owner oder jemand mit einer höheren Rolle.

---

## 2. Kanäle anlegen

| Kanal | Wer sieht ihn? | Zweck |
|---|---|---|
| `#nicht-schreiben` (Honeypot) | `Member` | Köder. Ganz oben in der Kanalliste platzieren, Spam-Bots schreiben fast immer in den ersten Kanal. Als Text z.B.: *„⚠️ Nicht hier schreiben – du wirst automatisch gesperrt.“* |
| `#mod-log` | nur Mods/Admins + Bot | Hier meldet der Bot alles |
| `#quarantäne` *(optional)* | `Quarantäne`-Rolle + Mods | Gesperrte User können hier mit den Mods reden |

Für die optionale Rolle `Quarantäne` gilt: Sie braucht **keine** Berechtigungen. Sie bekommt nur im Kanal `#quarantäne` *Ansehen + Senden* freigeschaltet.

---

## 3. Bot erstellen

1. https://discord.com/developers/applications → **New Application**
2. Links auf **Bot** → *Reset Token* → Token kopieren. Er kommt später in die `.env`, gib ihn **niemals** weiter.
3. Auf derselben Seite **Server Members Intent** einschalten.
4. Unter **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Berechtigungen: *Rollen verwalten, Mitglieder bannen, Nachrichten verwalten, Kanäle ansehen, Nachrichten senden, Nachrichtenverlauf lesen*
   - Den Link öffnen und den Bot einladen
5. In Discord die Bot-Rolle **ganz nach oben** ziehen (siehe Rangfolge).

IDs kopieren: Discord → Einstellungen → Erweitert → **Entwicklermodus** an. Dann Rechtsklick auf den Server, Kanal oder die Rolle → *ID kopieren*.

---

## 4. Bot starten

```bash
cp .env.example .env        # dann .env ausfüllen
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python bot.py
```

Der Bot muss dauerhaft laufen (eigener PC, Raspberry Pi oder ein kleiner VPS). Er speichert die weggenommenen Rollen in `data/quarantine.json`. **Diese Datei nicht löschen**, solange jemand in Quarantäne ist.

---

## Befehle

Diese Befehle sehen nur Leute mit dem Recht *Mitglieder timeouten*. Anpassen kannst du das unter Servereinstellungen → Integrationen → Bot.

| Befehl | Was er macht |
|---|---|
| `/quarantine @user [reason]` | Nimmt alle Rollen weg und merkt sie sich |
| Rechtsklick auf User → Apps → **Quarantine** | Dasselbe, schnell vom Handy aus |
| `/release @user` | Gibt die gespeicherten Rollen zurück |
| `/quarantine-list` | Zeigt, wer gerade gesperrt ist |
| `/rules-button [text] [label]` | Postet den „Regeln akzeptieren“-Button in den aktuellen Kanal (nur für Leute mit *Server verwalten*) |

### Gut zu wissen
- **Rollen nur mit `/release` zurückgeben.** Gibt jemand einem gesperrten User per Hand eine Rolle, nimmt der Bot sie sofort wieder weg. Das passiert absichtlich, damit Onboarding oder ein Rejoin die Sperre nicht umgehen.
- Leaven und neu joinen hebt die Quarantäne nicht auf.
- Ein **Admin mit Administrator-Recht** könnte den Bot kicken, bevor du reagierst. Gegen einen Admin, der wirklich durchdreht, hilft nur: so wenige Leute wie möglich mit Administrator-Recht.
- `HONEYPOT_ACTION=quarantine` (Standard) ist freundlich zu gehackten Accounts von echten Freunden: Du prüfst und gibst sie wieder frei. Bei sehr viel Bot-Spam stellst du auf `softban` oder `ban` um.
