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
genau einmal vorkommt. Andere Versionen warten auf die Folgeanfrage mit einer
anderen Zeit (Serviceprogramm 7.5.4.6 der M202: 30 statt 25 Sekunden); diese
Frist wird verlängert, wenn sie genau einmal an dieselbe Warteroutine geht wie
die 60-Sekunden-Frist des Menüs.

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
kurzer Code-Fenster aus ihrer Umgebung und aus der Umgebung der Befehle, die
sie als Operand nennen. Absolute Adressoperanden zählen dabei als Null, denn
ein anderer Stand – auf der 4-MB-Platine liegen die Variablen Megabytes
entfernt – unterscheidet sich genau in diesen Bytes. Mit den Prüfsummen sucht
`firmware_relocation.py` die Adressen in der gewählten Datenbank:

- Variablen und Tabellen liest es aus den Befehlen, die sie nennen; der Inhalt
  einer Tabelle darf sich ändern.
- Eine Codestelle liegt nur dort, wo auch ihre eigenen Bytes oder die direkt
  davor stehen; davor eingefügter Code zieht sie nicht an die alte Stelle.
- Zwei Adressen an derselben Stelle gelten als nicht gefunden.

`scripts/firmware_builds.json` ergänzt für Stände, deren Adressen Befehl für
Befehl geprüft wurden (Schlüssel: SHA-256 der entschlüsselten Runtime), was
die Suche nicht finden kann, etwa eine Variable, die der andere Stand in einer
Struktur hält. Die Bridge läuft dann mit denselben Quelltexten und den
gefundenen Adressen (`DB_VERSION_ADDRESSES_ADAPTED`). Fehlt eine Adresse,
startet der Emulator nicht.

Kommt eine Adresskonstante in `m68k_database_bridge.py` oder
`cabinet_controls.py` hinzu, wird die Tabelle aus der entschlüsselten Runtime
des geprüften Stands neu erzeugt:
`python scripts/firmware_relocation.py --build RUNTIME`.

## Datenbankplatinen mit 2 und 4 MB

Die Modulfamilie im Kopf der Datenbankdatei bestimmt die Platine
(`scripts/database_profile.py`): 616404 läuft mit 2 MB RAM, 616406 mit 4 MB.
Schlüssel, Entschlüsselung, Loader-Einsprünge und der Konfigurationsbereich
am Speicherende folgen daraus; Loader und Datenbank müssen zur selben Familie
gehören (`DB_BOARD_DETECTED`).

Jeder Stand sperrt seine Interruptquellen mit `ORI #$0500,SR`. Der 2-MB-Stand
gibt sie mit Maske 0 frei, der 4-MB-Stand läuft durchgehend mit Maske 4. Die
Bridge stellt Timer- und UART-Interrupts zu, solange die Maske unter 5 liegt.

Der Startrahmen INITVIDEO der 4-MB-Platine ist ein Byte länger (40 statt 39).
Die Bridge ergänzt darin die Uhrzeit; die Gehäusebytes ergänzt sie nur im
39-Byte-Rahmen, für den sie aus dem Protokoll des Geräts bekannt sind.

Die SRAM-Bibliothek reicht neben `FbwfEnableFilter` und `FbwfIsFilterEnabled`
auch `FbwfSetCacheThreshold` und `FbwfGetMemoryUsage` an die Bibliothek der
Karte weiter; neuere Spielprogramme importieren alle vier.

## Factory und Konfiguration

Ein Factory-Modul besteht aus einer festen Rücksetzroutine: Sie löscht das RAM
ab 0x10000, macht die Prüfsumme des geladenen Programms ungültig, schreibt die
Marke `INIT` und übergibt an den Loader. Ohne Factory-Datei führt die Bridge
dieselbe Routine selbst aus (`DB_FACTORY_EXECUTED source=built-in-routine`).

Die Konfiguration eines Spielepakets ist ein kleines Modul: Es legt eine
512-Byte-Tabelle (die Spielliste) 0x400 Byte und eine 24-Byte-Kennung 0x80
Byte unter das Speicherende und übergibt an den Loader. Wo Tabelle und Kennung
im Modul stehen, nennt sein Code selbst; die Bridge liest beide Quellen von
dort. Ein Spielepaket, das seine Spielliste selbst mitbringt, wird ohne
Konfiguration gestartet; das Feld im Starter bleibt dann leer
(`DB_CONFIG_RAM_UNCHANGED`). Fehlt einer Datenbank der 4-MB-Platine die
Konfiguration, zeigt sie „Spielpaket konfigurieren“ und wartet.

## Startdatum

Im Kopf der Datenbankdatei steht das Jahr, ab dem das Programm seine Uhr für
gültig hält (zwei Ziffern bei 0x8C: 2007 bei Magie 90 und 88, 2015 bei Magie
202). Mit einer früheren Uhr bleibt es mit dem Uhrenfehler `F_UHR` stehen. Der
Starter vergleicht das Startdatum der Emulationseinstellungen damit und
startet bei einem früheren Jahr nicht.

## Zulassungskarte und Tasten

Die Datenbank liest von der Karte Nummer, Profil und vier Bytes ab `0x0D`. Das
Profil muss zu dem Gerätetyp gehören, als der sich die Datenbank meldet; die
Magie 202 wählt den Gerätetyp nach dem Profil der Karte (z. B. 2002 für
ADP2003 „CC5“) und damit auch die Tastenbelegung. Eine
Karte, die die Datenbank noch nicht kennt, beschreibt sie mit ihrer Nummer; die
Bridge nimmt Schreibzugriffe nur im Arbeitsspeicher an, die gewählte Datei
bleibt unverändert.

Die Modulfamilie bestimmt die Tasten des Bedienfelds. M88/M90 behalten
„Autostart“, „Einsatz“, „Maxeinsatz“ und „Automatik“. M202 verwendet stattdessen
„Level“, „MaxLevel“ und „Info“ sowie zusätzlich „Einsatz / Umbuchen“
(Tasten-ID 13). Die elektrischen Tastenwörter liest die Bridge weiterhin aus
der Tabelle des von der Firmware gewählten Gerätetyps.

Der interne Kontakt „starten“ (ID 3) wird als „Autostart“ angezeigt;
„Start“ verwendet ID 6. Bei M202 werden steigende
Flanken aktiver High-Kontakte bei Offset +8 veröffentlicht und fallende
Flanken aktiver Low-Kontakte bei +0x88. Die ursprünglichen M202-Abfrageroutinen
prüfen die oberen Mapping-Bits mit Maske 0x18. Diese Verarbeitung hat einen
eigenen Familienzweig; M88/M90 verwenden weiterhin ihre bisherige Verarbeitung.

Auch die Touch-Weiterleitung ist familienabhängig: M202 sendet den
11-Byte-Rahmen `01 02 33 00 … 04`, M88/M90 `01 02 41 00 … 04`.
Ein vollständiger Touch-Rahmen wird gepuffert und zusammen an COM3 geschickt.
Die MCU-Ausführung über den Debugger zieht die drei Meldungen eines kurzen
M202-Taps über mehrere Sekunden auseinander. Ist der Hostkontakt beim
Eintreffen seiner ersten Meldung bereits losgelassen, hält
`m202_touch_transport.py` seine Punkte bis zur nativen Freigabe zurück. Es
quittiert die zurückgehaltenen Meldungen zur MCU und lässt nur die
PC-Quittung der Freigabe zur MCU durch. Unveränderte Liftoff-Punkte zählen
dabei nicht als zweiter Druck. Touch-Rahmen gehen einzeln an den PC, der
nächste erst nach dessen Quittung `06 03 00 00` des vorigen, wie bei der
Original-DB: Das Spiel verarbeitet je Empfangsereignis nur einen Rahmen, ein
zweiter Rahmen im selben Paket bliebe bis zu den nächsten Bytes liegen, und
das Menü wiederholte den gehaltenen Knopf nach 600–800 ms. Hat der PC einen
Punkt schon erhalten, während der Hostkontakt noch lag, bekommt er die
Freigabe sofort beim Loslassen; die verspäteten Liftoff-Meldungen der
Firmware zu diesem Kontakt werden lokal quittiert. Touches für das
Serviceprogramm gehen an dessen eigenes Objekt; dort meldet die Firmware nur
Kontakt- und Liftoff-Punkt, keine Freigabe, und das Spiel klickt je Punkt in
den Service. Folgt dem Liftoff-Punkt nicht binnen 0,4 Sekunden eine Freigabe,
gehen die Punkte allein an den PC, und dieses Ziel erhält keine künstliche
Freigabe. Gehaltene Kontakte bleiben im normalen Stream, verschiedene
Drag-Positionen bleiben erhalten, und Kalibrierung läuft weiterhin im
Controller. Zeitabhängige Doppelklickfilter werden nicht eingeführt.

Bei M88/M90 bleibt der Schalter „Automatik“, wie ihn das Board liefert,
solange er nicht benutzt wird. Bit 15 im Tastenwort der Firmware kehrt den
Pegel um.

Bei M202 startet QEMU mit `vmport=off` und ohne USB-Zeigegerät. Der 8042-
Controller bleibt vorhanden, weil XP ihn bereits im Bootloader braucht.
Vor dem Start setzt die Offline-Vorbereitung nur den Windows-Mausklassendienst
`Mouclass` auf deaktiviert; die ursprünglichen Werte und die SYSTEM-Hive
werden unter `NVRAM/m202-pointer-backup` gesichert. Tastatur und serielle
Touchsteuerung bleiben erhalten. Dafür werden die vorhandenen WSL-Werkzeuge
`ntfs-3g` und `python3-hivex` verwendet.
Klicks im QEMU-Fenster und in der Vorschau laufen über die virtuelle
Touchsteuerung des Bedienfelds. Die bisherige M88/M90-Startkonfiguration
bleibt erhalten.
