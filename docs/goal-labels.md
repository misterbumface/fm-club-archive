# Goal labels: the game's own words against the save's shot events

Started 2 Nov 2026. Goal: learn what the highlight events in `match_stats_cfg.dat` say about each shot (header, volley,
free kick, cross, distance), so the papers can tell how a goal was scored as a fact the fact checker can check. Nothing is
built on these codes until each one has at least five labelled matches and no misses.

`tools/shot_events.py <save.fm>` prints every shot event with its player, so a new label can be matched to its bytes.

## What is known

Each detailed match record (`match_stats_cfg.dat`, the reports `read_match_reports` reads) holds, besides the player
records, a list of highlight events. An event: `02 <side 0/1> <slot> <code> <tick u16> 00 00 <minute> 00`, then detail bytes
from +10. The tick is the match clock of the shot records (their bytes 11-12): a shot event and its shot record carry the
same tick (+-4), which links an event to its shot exactly (836 goal events in the save of 9 Feb 2064).

| Finding | Evidence | Status |
|---|---|---|
| slot = the player's index in his side's player records (team-sheet order) | 836 of 836 goal events linked by tick | proven |
| code 38 = yellow card | Strasbourg 0-1 OL, 8 Feb 2064: OL cards 19', 22', 23' (Álvaro, Devoy, Franco) | firm |
| code 1 = a shot; detail: value u32 at +10, type byte at +14, then bytes +15.. | see labels | firm |
| type `03` = header | Paulo Roberto 71', Pardal 59', Hernando 2', Etxaniz 56', Falah 63', Themistokleous 36' (6 labels); 200 of 701 inside-the-box goals, 0 of 82 outside-the-box goals | 5 labels |
| bit `0x80` in the type (`80`, `81`, `85`, `a1`, `a5`) = something of shots in the box | 147 inside-the-box goals, 0 outside | likely; meaning unknown |
| bit `0x04` = volley | Broadfoot 65' `05`, Ghiocel 48' `85`, Rodrigo 23' `05` "thunderous volley" | likely (3) |
| bit `0x80` | in drives `a1` (Ángel, Sheminant, Valera: no error told), volley `85` (Ghiocel, after an error), finish `81` (Ondo, after an error); only in the box | unknown: not "after an error" alone |
| bit `0x20` = drive (low, hard) | `a1`: Ángel 43' "low drive" (h 0.46), Sheminant 74' "rifled drive which kept low" (1.00), Valera 15' "skidding drive" (0.00); `21`: Uranga 17' "skidding drive" (0.19). So the type is bits: `01` foot, `02` head, `04` volley, `20` drive, `80` unknown (never outside the box) | likely (4) |
| type `01` = finish | Jones 8' "deftly executed finish", Odogwu 54' "accurate finish", Formicola 33' "accurate finish", Javier 81' "tidy finish", Brugué 18' "placed shot"; also Diarra 53' (free kick: marked elsewhere) | 5 labels |
| byte +15 `88` = free kick | Diarra 53' (the others: `10` or `40`) | guess (1) |
| the value at +10 | rises with distance (headers lowest), but 48 of 82 outside-the-box goals are under 16 500 (inside: median 8 700, outside: median 15 200); Javier's "finish from 12 metres" is 8 769, Etxaniz's "close range header" 12 834 | NOT the distance (the game's text knows the distance: it is stored elsewhere or computed) |
| code 47 | at minutes with no goal (Chelsea v Shamrock 13', 24', 33', 42') | unknown, not "goal" |
| the event list | only some moments: Marks 90+2' and Myers 90+3' (Chelsea v Shamrock) have no shot event | a selection |

Ruled out: the shot record's flag bytes (they repeat the goal-mouth zone and a high/low band; header, volley and free kick
share them); the goal list (scorer, side, kind 0-5, minute; also in the match news item in `news.dat`); the player's
11-byte event items (empty for these scorers); the `.apm` files (match-engine data of one team each). The words of the
game's news are written at display time; the facts come from these stores.

Whole-save counts (save of 9 Feb 2064, 1 198 shot events): type `01` 351 (median value 12.3k), `03` 213 (5.8k), `00` 310,
`21` 114 (12.6k), `a1` 95 (8.3k), `80` 62 (always 1.5k), `05` 50 (7.7k), `02` 34, `85` 33 (5.3k), `81` 22. The players'
"headers" figure (aerial duels) does not tell headed shots apart: 96% of `03` events and 92% of `01` events have one.
The test that works is the scorer's goals from outside the box (his match record counts them): types of the 82 goals of
scorers whose goals were all from outside: `01` 54, `21` 19, `05` 9; of the 701 all-inside: `01` 224, `03` 200, `a1` 90,
`21` 78, `05` 40, `85` 30, `81` 19, `a5` 8.

The user's reading (2 Nov 2026), which the labels so far bear out: the type byte is the kind of shot the game's text
names (finish, drive, header, volley); how the ball went in (low, top corner) is the shot record's height and across.

## Labels

Source: the game's match report text or the user watching the goal. Bytes from the save of 9 Feb 2064
(the save written at 20:11 that day). "Event" is the shot event's value, type and the bytes after the type.

| Game (date) | Goal | The game says | Event: value, type, then | Shot record: height, across |
|---|---|---|---|---|
| Bolton 3-3 Newcastle, FA Cup (9 Feb 2064) | Kyle Jones 8' | "a deftly executed finish" | 13 884, `01`, `10 08 00 04 40 01 00 01 10` | 1.48, 2.95 |
| | Ángel 43' | "a low drive" | 6 749, `a1`, `40 00 00 01 80 00 00 01 01` | 0.46, 2.65 |
| | Amos Odogwu 54' | "an accurate finish" | 8 435, `01`, `10 42 10 02 00 00 00 a1 00` | 0.18, -3.18 |
| | Ally Broadfoot 65' | "a powerful close range volley into the top corner"; user: from the penalty area | 10 748, `05`, `40 48 00 02 c0 00 00 88 00` | 1.67, -1.42 |
| | Paulo Roberto 71' | "a good header from just inside the six-yard box"; user: from a cross | 6 608, `03`, `40 40 00 01 c0 00 00 22 01` | 1.58, -3.38 |
| | Tegan Sheminant 74' | "a rifled drive which kept low" | 11 592, `a1`, `40 40 00 02 80 00 00 08 01` | 1.00, 2.99 |
| Strasbourg 0-1 OL, Ligue 1 (8 Feb 2064) | Aoula Diarra 53' | "a free kick into the top corner from the edge of the area" | 22 661, `01`, `88 00 00 00 c4 40 00 00` | 1.74, -3.45 |
| Nottingham Forest 0-3 Chelsea, Premier Division (3 Feb 2064) | Nacho Valera 15' | "a skidding drive" | 2 836, `a1`, `40 40 00 01 80 40 00 00 00` | 0.00, -1.79 |
| | Marcelo Formicola 33' | "an accurate finish from inside the penalty area"; user: aimed high, keeper got a touch | 12 714, `01`, `50 48 00 08 c0 40 00 02 10` | 1.46, -0.81 |
| | Marcelo Formicola 45' | "a powerful close range effort" | no shot event | |
| Girona 1-0 Barcelona, La Liga (8 Feb 2064) | Jesús Hernando 2' | "a close range header" | 8 427, `03`, `40 00 00 01 c0 40 00 41 01` | 2.04, -2.50 |
| Oviedo 1-3 Espanyol, La Liga (8 Feb 2064) | José Ramón Uranga 17' | "a skidding drive" | 4 947, `21`, `00 08 00 02 80 40 00 24 10` | 0.19, -3.06 |
| | Delfí Català 45' | "a volley from within the six-yard box" | no shot event | |
| | Mikel Etxaniz 56' | "a close range header" | 12 834, `03`, `40 40 00 01 c0 40 00 02 01` | 1.59, 2.90 |
| | Javier 81' | "a tidy finish from 12 metres" | 8 769, `01`, `00 80 02 02 e0 40 00 01 10` | 1.71, -1.81 |
| Chelsea 6-1 Liverpool, Premier Division (22 Dec 2063) | Júnior 5' | Goal of the Month, December: "an accurate finish from 23 metres" | 22 220, `01`, `90 08 00 00 88 40 00 04 10` | 0.07, 2.40 |
| FC Bayern 5-0 Eintracht Frankfurt, Bundesliga (9 Feb 2064) | Teodoro Brugué 18' (his other goal, 40', is a header `03`, so this is the one) | "a placed shot which hit the inside of the post before crossing the line" | 14 548, `01`, `10 08 00 02 00 45 00 01 10` | 0.51, 1.34. The user watched it: a blistering shot through the centre, from just inside the box (about 15-16 m), keeper a metre or two off his line. So "hit the inside of the post" was the game's own colour: the shot record beats the game's prose. His 40' header: user saw it go in "top centre"; record 1.29, -1.34 |
| FC Nantes 2-0 RC Lens, Ligue 1 (9 Feb 2064) | Abdallah Falah 63' | "a good header from just inside the six-yard box" | 2 335, `03`, `40 40 00 01 80 40 00 02 01` | 0.67, -2.29 |
| Rotherham 1-2 Middlesbrough, FA Cup (9 Feb 2064) | Kostas Themistokleous 36' | "a close range header" | 10 397, `03`, `40 40 00 01 00 41 00 01 01` | 0.29, -2.30 |
| | Flavius Ghiocel 48' | after an error by Goñi, "a close range volley into the top corner" | 13 964, `85`, `40 40 00 02 40 41 00 01 09` | 1.35, 2.45 |
| | Rodrigão 82' | "strike"; user watched it: his shot was deflected back to him and he headed it into the corner | no shot event | goal 0.40, 2.16; his blocked shot (1.46, 1.60) in the same second, 81:08 |
| Manchester City 0-2 Everton, FA Cup (9 Feb 2064) | Rodrigo 23' | "a thunderous volley" | 12 372, `05`, `10 08 00 00 04 40 00 02 01` | 1.14, 0.95 |
| | Manuel Ondo 33' | after an error by Calamaro, "a close range finish into the bottom right corner" | 3 783, `81`, `40 00 00 01 00 44 00 08 09` | 0.55, -0.15 (central: the game's "corner" is loose, as Brugué's "post") |
| Chelsea 3-1 Shamrock Rovers, Champions League (30 Jan 2064) | Francisco Pardal 59' | user: header from close in, edge of the six-yard box near the post | 4 397, `03`, `40 40 00 01 00 21 08 41 01` | 1.00, -2.68 |
| | Jake Marks 90+2' | user: shot from outside the box, left of the D (about 22-25 m) | no shot event | 1.96, 3.56 |
| | Ken Myers 90+3' | user: shot from outside the box, right of the D (about 22-25 m) | no shot event | 0.15, -2.76 |

Labelled in the game's text but not in the save's detailed reports (no event to compare): Cosenza 2-3 AC Milan (9 Feb 2064:
Alan Black 35' close range header, Tariq Tanser 50' powerful close range effort, Ion Lazar header from inside the six-yard
box, Borgogni 86' freak goal, Neri 21' own goal); Freiburg 1-1 Gladbach (9 Feb 2064: Unai Martínez 56' volley from within
the six-yard box, Salgado 90' own goal).

Distance (the game's words against the value): Júnior "23 metres" 22 220; Javier "12 metres" 8 769; Diarra "the edge of the
area" 22 661; Pardal close in 4 397; Etxaniz "close range header" 12 834. "Close range" goals with large values: Etxaniz header 12 834, Themistokleous header 10 397, Ghiocel volley 13 964: the
game's "close range" is loose, or the value is not the shot's distance (the length of the pass or cross before it?).
Brugué 18' (user: shot from about 15-16 m, keeper 1-2 m out) 14 548 fits both. An idea to test: the distance to the goalkeeper
(a keeper off his line makes it short of the distance to goal; Etxaniz does not fit unless "close range" is loose).
Premier Division Goal of the Month, Aug 2063 - Jan 2064 (screenshot, 2 Nov 2026): only Júnior's goal is in the save's detailed
reports; the rest (Rotherham, Sheffield United, Luton, Reading, Newcastle and Arsenal games, or older than late November) are not.

The user's replays of three Premier Division Goal of the Month goals (2 Nov 2026) show each shot taken from about the
game's distance: Rodrigão (Rotherham v Middlesbrough, 19 Jan) "19 metres", at the edge of the box; Jorge Ramírez (Sheffield
United v Wolves, 1 Jan) "23 metres"; Alberto (Luton v Plymouth, 5 Jan) "a 28 metre free kick". So the game's distances are
true shooting distances; none of the three matches is in the save's detailed reports, so there is no value to compare.

Goal of the Month, Sky Bet Championship, January 2064 (the game gives the distance): Devon Jones (Blackburn at MK Dons, 26
Jan) "a spectacular close range overhead kick"; Matthew Dickson (Leicester v Brighton, 26 Jan) "a fierce effort from 20
metres out" after a team move; Regan Cooper (Charlton v Cardiff, 26 Jan) dribbled from his own half, "a terrific chip from
20 metres". None of the three matches is among the save's detailed reports (neither the save of 9 Feb 20:11 nor the backup
of 15:14): the game details only some matches (about 518 at a time: the user's clubs, their competitions and some others),
so award goals help only from those.

## Facts from the shot records alone

A rebound: a goal in the same second as (or a few seconds after) the same player's blocked or saved shot. Rodrigão 81:08
(Rotherham v Middlesbrough, 9 Feb 2064): blocked, then the goal, both at 81:08, as the user saw. This needs no event code.
Open: the game also says "deflected shot". A deflected goal may look the same (blocked, then the goal) or be one goal shot;
one labelled "deflected shot" will tell. Not a clue: a shot entry's part count. An entry groups one player's shots that share
the zone flags, minutes apart (Sheminant 23:01 blocked and 73:54 goal are one entry), so its parts are not stages of a shot.

## Wanted

Goal of the Month or Week entries (they give the distance) from the Premier Division or the Champions League, whose
matches the game details. Goals the game describes with: a deflected shot, a cross, a corner, a through ball, a penalty, a long shot "from 25 yards", a free kick, a
rebound, a one-on-one, a chip, the left or right foot. Each one fixes or breaks a code.
