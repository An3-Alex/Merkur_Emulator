# M90-Emulator

Ein Windows-Programm, das einen Merkur-M90-Automaten auf dem PC nachbildet.
Es startet den virtuellen Spiel-PC und die Datenbank, richtet eine Arbeitskopie
des CF-Images ein und bietet ein Bedienfenster für Touch, Tasten, Türschalter,
Service und Spielgeldeinwurf. Ton und ein optionales Live-Protokoll sind enthalten.

[Emulator herunterladen](https://github.com/An3-Alex/Merkur_Emulator/releases/latest)

## Starten

1. `M90-Emulator.exe` öffnen und die benötigten Abhängigkeiten über den Starter einrichten.
2. Eigenes CF-Image, einen getrennten Arbeitskopie-Pfad und die passenden
   Datenbankdateien auswählen: Loader, Factory und Spielepaket; die
   Konfiguration nur, wenn das Spielepaket eine hat. Zulassungskarten-EEPROM
   und QXL-Treiber werden ebenfalls benötigt, die SwiftShader-DLL nur für den
   Grafikpfad SwiftShader.
3. Beim ersten Mal „Frisches Image einrichten“ verwenden. Danach „Emulator starten“.
   Eine bereits eingerichtete Arbeitskopie kann direkt gestartet werden.

Der erste Start nach „Frisches Image einrichten“ dauert etwa zwei Minuten
länger: Das Spiel übernimmt dabei die Einstellungen der Datenbank und startet
Windows einmal neu.

Mit „Alles beenden“ werden die zum Emulator gehörenden Prozesse beendet.
Dateipfade und Einstellungen bleiben für den nächsten Start gespeichert.
Das Original-CF-Image wird nicht verändert. „Emulator deinstallieren…“ ganz
unten in den Einstellungen entfernt Laufzeitdaten, Protokolle und
Einstellungen, auf Wunsch auch die Arbeitskopie.

[SwiftShader und QXL vorbereiten](docs/grafikdateien.md): passende Dateien,
Downloadquelle für QXL und Auswahl im Starter.

## Grafik und Voraussetzungen

Windows 10/11, die im Starter angezeigten Abhängigkeiten und ausreichend freier
Speicherplatz für die Arbeitskopie werden benötigt.

Als Grafikpfad ist QEMU-3dfx voreingestellt: Beide Bildschirme nutzen die
Host-Grafikkarte, mit getrennten Fenstern für unten und oben. Das Bedienfeld
zeigt weiterhin den unteren Spielbildschirm. SwiftShader rendert in Software
und ist nur die Alternative, falls QEMU-3dfx auf dem PC nicht funktioniert;
umgestellt wird unter „Emulationseinstellungen → Grafikpfad“.

Der Start kann mehrere Minuten dauern.

## Andere CF-Karten und Datenbanken

CF-Karte und Datenbank gehören zusammen; viele unterscheiden sich nur in den
enthaltenen Spielen. Ein CF-Image mit anderer Spielversion wird wie das
geprüfte eingerichtet. Der Starter nennt dabei jede Datei der Karte, die von
der geprüften Version abweicht.

Unter „Emulationseinstellungen → Datenbank-Schlüssel (D3)“ steht standardmäßig
`auto`: Ist der Schlüssel einer gewählten Datenbank unbekannt, sucht ihn der
Starter einmalig (je nach CPU bis etwa 30 Minuten) und merkt ihn sich. Ist
eine Datenbank ein anderer Stand desselben Datenbankprogramms (z. B. Magie 88
CC4), findet die Bridge ihre Adressen darin selbst; der Starter meldet das.
Findet sie sie nicht, startet der Emulator nicht.

Die Zulassungskarte muss zum Gerätetyp passen, als der sich die Datenbank
beim Start meldet (`ADP…`); sonst zeigt das Spiel „keine Zulassungskarte
erkannt“. Die Taste „Automatik“ im Bedienfeld bucht bei Geräten ohne
automatische Umbuchung vom Geldspeicher in den Punktespeicher.

## Protokolle

Jede Logart nutzt eine feste Datei im Laufzeitordner `logs` (bzw. `NVRAM` im
Gast). Ab 10 MB beginnt sie neu; der vorige Inhalt bzw. der letzte Lauf bleibt
als `*.old.*` erhalten.

CF-Images, Datenbank-Firmware und proprietäre Gastdateien werden nicht
mitgeliefert. Die Quellen der enthaltenen Open-Source-Komponenten liegen beim
Release. Der Projektquellcode lässt sich mit Git herunterladen; weitere
technische Informationen stehen unter [docs](docs).

## Geprüfte Kombinationen

Mit diesen CF-Karten und Datenbanken läuft der Emulator.

**Kombination 1:** CF-Karte mit der Startanzeige `M440_945_KOMBI_V20`,
`SW-Date: 20121212`, `ADP - LOADER VERSION V7.0.0.5`.

| Teil | Datei | SHA-256 |
| --- | --- | --- |
| CF-Karte | `game.exe` | `27c4553927397b1e8443d6caea12e5b4e7282e4948b67b85c80c4db0d1d0427d` |
| Loader | `Loader_61640403_L5.0b_2MB.bin` | `b0768c65b34834c7a740615d2b0abdb470aec012fe4dc4a3c11531efa221e109` |
| Factory | `FactoryReset_61640403.xc` | `4f088db4af5f4a5d112a003ef312eb19b4388c25902ffa03f75742379a0cd5c4` |
| Konfiguration | `M90_Las_Vegas.bin` | `dce3a865b742123c95ea4f0b14fa16f287ddf90cd86432f68b2301b70a919783` |
| Spielepaket | `Magie_90_CC4.bin` | `593cf4b3a1ccc83f206e1492e44b9d303ea3c05990059b8659d8308da1dc2ee8` |

**Kombination 2 (Magie 88 CC4):** CF-Karte mit der Startanzeige
`M440_945_KOMBI_V18`, `SW-Date: 20121212`, `ADP - LOADER VERSION V7.0.0.3`.
Dieses Spielepaket hat keine Konfiguration. Die Datenbank meldet sich als
`ADP1444`; die Zulassungskarte braucht dazu das Profil 1150. Getestet sind
Start, Spielauswahl, Münzeinwurf, Umbuchung mit „Automatik“ und das Laden
eines Spiels.

| Teil | Datei | SHA-256 |
| --- | --- | --- |
| CF-Karte | `game.exe` | `7f1f7fcd9c0d15c345decc2f14102502905a5507eff3142e919987d8805612ee` |
| Loader | `61640400.bin` | `b0768c65b34834c7a740615d2b0abdb470aec012fe4dc4a3c11531efa221e109` |
| Factory | `61640403.bin` | `f4fa79dea48398c30ca717264ec82a824b6a93127cdf14c1b3f628bb53bc6d1c` |
| Spielepaket | `MERKUR_MAGIE_88_CC4.bin` | `acb5f53a21daee63330e98e3e57f081f4716c697de8f35709425736c5e127d50` |
