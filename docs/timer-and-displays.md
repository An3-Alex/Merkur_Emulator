# Timer, Anzeigen und Laufzeit-Updates

## Datenbank-Timer

Die CC4-Routine bei `0x1825C` programmiert ACR `F0`, Counter-Preset `0074`
und IVR `40`; der alternative Zweig schreibt `003A` und IVR `41`.
Die Bridge übernimmt diese Schreibwerte nach der I/O-Initialisierung und
beobachtet spätere Firmware-Schreibzugriffe.

ACR[6:4] = 111 wählt Timerbetrieb mit X1/16. Counter-Ready entsteht einmal
pro vollständiger Rechteckperiode: `2 * 16 * preset / X1` Sekunden.
Quelle: [NXP SCC68681, Timer und ISR[3], Seiten 16–17](https://www.nxp.com/docs/en/data-sheet/SCC68681.pdf).

Bei 3,6864 MHz (Einstellung „DUART-Eingangstakt“) sind das 1,006944 ms
(`0074`) bzw. 0,503472 ms (`003A`). Unterstützt sind dieser Timerbetrieb und
die beiden CC4-Vektoren; andere Quellen/Vektoren und Presets null/eins werden
abgelehnt. Ein Bruchteil-Akkumulator verhindert Rundungsdrift. Höchstens 128
Interrupts pro CPU-Zeitscheibe werden nachgeholt; bei Umprogrammierung wird
der ausstehende Tick-Batch verworfen. Das ist eine Host-Bridge, kein
zyklusgenaues DUART-/CPU32-Gerät.

## Erster Start einer Arbeitskopie

Mit dem ersten INITVIDEO erhält das Spiel die Automaten-Einstellungen der
Datenbank, etwa die Bildschirmgrößen. Weichen sie von den auf der CF-Karte
gespeicherten ab, schreibt das Spiel sie nach `NVRAM/machineSetup.txt` und
startet Windows einmal neu, statt die Systeminfo zu senden.

Die Datenbank fragt die Systeminfo nach der INITVIDEO-Bestätigung ab und
beginnt nach etwa 40 Sekunden ohne Antwort mit ihrem Startcode von vorn; der
Windows-Neustart dauert länger. Die Bridge hält die Datenbank deshalb an,
sobald die Abfrage zehn Sekunden unbeantwortet ist, und wiederholt INITVIDEO
weiter auf der Leitung. Bestätigt das neu gestartete Spiel, sendet die Bridge
die Abfrage der Datenbank erneut; mit der Systeminfo läuft die Datenbank
weiter. Im Ereignisprotokoll stehen dazu `DB_HANDSHAKE_WAITS_FOR_GUEST`,
`DB_INITVIDEO_GUEST_RESTARTED` und `DB_HANDSHAKE_CONTINUES`. Der erste Start
dauert dadurch etwa zwei Minuten länger als die folgenden.

## Zwei Anzeigen

Der Spiel-PC hat zwei QXL-Anzeigen mit je 800×600. Die D3D9-Bridge des Gasts
ordnet logischen Adapter null der Windows-Primäranzeige und Adapter eins der
Sekundäranzeige zu, positioniert die Spielfenster rahmenlos und hält beide
Geräte nicht-exklusiv. Ein fehlender zweiter Windows-Monitor führt zu einem
CreateDevice-Fehler statt zur Ausgabe beider Geräte auf einem Monitor.

Gerendert wird je nach Grafikpfad mit SwiftShader (Software) oder über
QEMU-3dfx auf der Host-GPU, siehe [QEMU3DFX.md](QEMU3DFX.md). Die
Bedienfeld-Vorschau zeigt immer die untere Anzeige (`lower`).

## Laufzeit-Updates vorhandener Arbeitskopien

Beim Start prüft der Starter die Arbeitskopie und aktualisiert nur eigene,
per SHA-256 bekannte Dateien: Display-Bootstrap (`WINDOWS/explorer.exe`),
CGOS-Shim (`WINDOWS/system32/Cgos.dll`), D3D9-Bridge (nur Softwarepfad),
irrKlang-Proxy, SRAM-Proxy `FBWFLIB.dll` und die Service-Anbindung. Unbekannte
Dateien werden nicht ersetzt. Zuerst wird ausschließlich lesend geprüft; nur
die ausgewählte, vom Original verschiedene Arbeitskopie wird beschreibbar
geöffnet. Der vorherige Stand bleibt mit Manifest unter
`NVRAM/m90-graphics-backups`; bei I/O-Fehlern werden bereits ersetzte Dateien
zurückgesetzt. Spiel, Datenbank, QXL-Treiber, SRAM-Daten und Registry bleiben
unangetastet.

## CF-Karten mit anderer Spielversion

Einrichtung und Laufzeit-Update führen für jede CF-Karte dieselben Schritte
aus. Die Dateien der Karte (Loader, Board- und Schreibfilter-Bibliothek,
Spielprogramm, Ton-Bibliothek, Serviceprogramm) müssen vorhanden sein; weicht
eine von der geprüften Version ab, nennt der Starter sie im Protokoll.

Die beiden Loader-Anpassungen (Geräte-Routine ohne Absturz bei fehlendem Gerät,
auf 5 Sekunden begrenzte Leerlauf-Wartezeit) werden an ihren Befehlsfolgen
erkannt, nicht an der Dateiversion. Adressen, die sich von Loader-Build zu
Loader-Build verschieben, bleiben beim Vergleich außen vor. Enthält ein Loader
die Befehlsfolge nicht, bleibt er unverändert. Das Serviceprogramm erhält immer
die SRAM-Anbindung; seine Datenfristen werden verlängert, wo derselbe Befehl
genau einmal vorkommt.

Das Spielprogramm darf nur in `WorkDir` liegen. Die SRAM-Bibliothek findet die
Importe des Spielprogramms über ihre Namen. QEMU erhält die Laufwerksgeometrie
aus dem Bootsektor der Karte, damit auch Karten mit anderer Größe starten.

Das Spiel vergleicht bei jedem Start die Liste, die es über seine Dateien
berechnet (`NVRAM/CheckTemp.txt`), mit der Liste der Karte
(`WorkDir/CheckSumAdpConfigTool.txt`). Bei einer Karte, deren Spieldaten
nachträglich geändert wurden, bleibt es mit „SYSTEM ERROR 28“ stehen. Das
Laufzeit-Update ersetzt dann die Liste der Arbeitskopie durch die berechnete;
die Liste der Karte bleibt als `CheckSumAdpConfigTool.txt.pre-m90` erhalten.
Der nächste Start läuft durch.

## Andere Stände des Datenbankprogramms

Die Bridge arbeitet mit den Adressen des geprüften Datenbankstands. In
`scripts/firmware_addresses.json` stehen zu jeder dieser Adressen Prüfsummen
kurzer Code-Fenster aus ihrer Umgebung, bei RAM-Adressen aus der Umgebung der
Befehle, die sie verwenden. Mit ihnen sucht `firmware_relocation.py` die
Adressen in der gewählten Datenbank. Die Bridge läuft dann mit denselben
Quelltexten und den dort gefundenen Adressen (`DB_VERSION_ADDRESSES_ADAPTED`).
Wird eine Adresse nicht eindeutig gefunden, startet der Emulator nicht.

Kommt eine Adresskonstante in `m68k_database_bridge.py` oder
`cabinet_controls.py` hinzu, wird die Tabelle aus der entschlüsselten Runtime
des geprüften Stands neu erzeugt:
`python scripts/firmware_relocation.py --build RUNTIME`.

## Konfiguration

Die Konfiguration eines Spielepakets ist ein kleines Modul: Es legt eine
512-Byte-Tabelle (die Spielliste) 0x400 Byte und eine 24-Byte-Kennung 0x80
Byte unter das Speicherende und übergibt an den Loader. Ein Spielepaket, das
seine Spielliste selbst mitbringt, wird ohne Konfiguration gestartet; das
Feld im Starter bleibt dann leer (`DB_CONFIG_RAM_UNCHANGED`).

## Zulassungskarte und Tasten

Die Datenbank liest von der Karte Nummer, Profil und vier Bytes ab `0x0D`. Das
Profil muss zu dem Gerätetyp gehören, als der sich die Datenbank meldet. Eine
Karte, die die Datenbank noch nicht kennt, beschreibt sie mit ihrer Nummer; die
Bridge nimmt Schreibzugriffe nur im Arbeitsspeicher an, die gewählte Datei
bleibt unverändert.

Das Bedienfeld kennt neben den Tasten jedes Geräts die Taste „Automatik“
(Spielstart-Automatik). Ihr Schalter bleibt, wie ihn das Board liefert, solange
sie nicht benutzt wird. Bit 15 im Tastenwort der Firmware kehrt den Pegel um.
