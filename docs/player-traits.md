# Player traits (preferred moves): where the save keeps them

Started 2 Nov 2026. The game's "Player Traits" (Plays One-Twos, Cuts Inside From Left Wing, Runs With Ball Often...) are not
read yet. They would tell a player's habits in previews and profiles ("a winger who cuts in from the left"): facts about the
player, never about one goal. (Which foot struck a goal is not in the save: Eric Geyer, left foot 20, right foot 8, curled
his goal against Lille with his right, 13 Feb 2064; a paper that guessed "left foot" was wrong.)

## The field

`game_db.dat`, each player's record: a u64 bit field, one bit per trait, at the birth date minus 27 (the 8 bytes ending 19
bytes before it). Found with Exhibits 10 and 11 (2 Nov 2026, the same moment of one game): the user unticked "Cuts Inside
From Left Wing" for Eric Geyer (uid 2002930942) and only that byte changed (birth date - 20: 0x02 -> 0x00). Over 19 988
players the field reads like traits: 9 297 have none, 4 608 one, 2 362 two, tailing off; about 1% read with many bits (a
record the reader does not align; to be dropped with a limit). Each bit has its own frequency, from 20 to 2 489 players.

The bit order follows the editor's Player Traits list read row by row (left column, right column, left, right...),
counting from 0: it predicted Byorn Eind's three new traits (Exhibit 12: Dictates Tempo, Looks For Pass Rather Than
Attempting To Score, Stops Play) as bits 22, 24, 26, and those were the bits that changed (his uid - 280 is the field's
first byte). The lower part of the right column does not fit yet: the rule says Runs With Ball Often 49 and Cuts Inside
From Left Wing 53, the save has 51 and 57, so 2 to 4 traits there were not seen in the screenshots.

| Bit | Trait | How known |
|---|---|---|
| 20 | Plays One-Twos | Geyer, and the row rule |
| 22 | Dictates Tempo | Eind (Exhibit 12), predicted by the row rule |
| 24 | Looks For Pass Rather Than Attempting To Score | Eind, predicted |
| 26 | Stops Play | Eind, predicted |
| 36 | Tries Long Range Free Kicks | Geyer, and the row rule |
| 51 | Runs With Ball Often | Geyer (his remaining bit), and Exhibit 13 |
| 57 | Cuts Inside From Left Wing | Exhibits 10/11 (toggle), and Exhibit 13 |

Exhibit 13 (Eind, his three earlier traits off, twelve of the right column's lower part on) read exactly twelve bits, all
odd: the right column holds the odd bits, in order. 33 Stays Back At All Times, 35 Tries Tricks, 37 Dives Into Tackles,
41 Gets Crowd Going, 43 Tries Long Range Passes, 45 Hits Free Kicks With Power, 47 Likes To Switch Ball To Wide Areas,
51 Runs With Ball Often, 55 Uses Long Throw To Start Counter Attacks, 57 Cuts Inside From Left Wing, 59 Crosses Early,
63 Plays Ball With Feet. Between them, 31 Arrives Late In Opponents' Area and 39 Cuts Inside From Both Wings (greyed in
the editor, so not ticked, and in their places). 49, 53 and 61 have no trait in the editor's list (149, 20 and 22 players
of 19 988 have them): traits FM24 does not show; keep them unnamed. The right column is complete; the left column is
proven for 20-26 and 36 and predicted for the rest (even bits, Dwells On Ball 30 ... Brings Ball Out of Defence 58),
which may have such gaps too.

The editor list as seen (top of both columns, 2 Nov 2026), row by row: Runs With Ball Down Left | Runs With Ball Down
Right; Runs With Ball Through Centre | Gets Into Opposition Area; Moves Into Channels | Gets Forward Whenever Possible;
Plays Short Simple Passes | Tries Killer Balls Often; Shoots From Distance | Shoots With Power; Places Shots | Curls Ball;
Likes To Round Keeper | Likes To Try To Beat Offside Trap; Uses Outside Of Foot | Marks Opponent Tightly; Winds Up
Opponents | Argues With Officials; Plays With Back To Goal | Comes Deep To Get Ball; Plays One-Twos | Likes To Lob Keeper;
Dictates Tempo | Attempts Overhead Kicks; Looks For Pass Rather Than Attempting To Score | Plays No Through Balls; Stops Play
| Knocks Ball Past Opponent; Moves Ball To Right Foot Before Dribble Attempt | Moves Ball To Left Foot Before Dribble Attempt.
Then the left column: Dwells On Ball, Tries To Play Way Out Of Trouble, Avoids Using Weaker Foot, Tries Long Range Free
Kicks, Does Not Dive Into Tackles, Hugs Line, Tries First Time Shots, Likes Ball Played Into Feet, Likes To Beat Opponent
Repeatedly, Possesses Long Flat Throw, Runs With Ball Rarely, Does not Move Into Channels, Refrains From Taking Long Shots,
Cuts Inside From Right Wing, Brings Ball Out of Defence; the right column: Arrives Late In Opponents' Area, Stays Back At
All Times, Tries Tricks, Dives Into Tackles, Cuts Inside From Both Wings, Gets Crowd Going, Tries Long Range Passes, Hits
Free Kicks With Power, Likes To Switch Ball To Wide Areas, Runs With Ball Often, Uses Long Throw To Start Counter Attacks,
Cuts Inside From Left Wing, Crosses Early, Plays Ball With Feet (rows possibly missed between).

Exhibit 14 (Eind, twelve of the left column's lower part on) read twelve even bits, in order: 32 Tries To Play Way Out Of
Trouble, 34 Avoids Using Weaker Foot, 38 Does Not Dive Into Tackles, 40 Hugs Line, 42 Tries First Time Shots, 44 Likes
Ball Played Into Feet, 46 Likes To Beat Opponent Repeatedly, 50 Possesses Long Flat Throw, 54 Does not Move Into Channels,
56 Refrains From Taking Long Shots, 58 Cuts Inside From Right Wing, 60 Brings Ball Out of Defence. Between them 30 Dwells
On Ball (not ticked), 36 Tries Long Range Free Kicks and 52 Runs With Ball Rarely (greyed), and 48, hidden like 49.

## The map (complete, 2 Nov 2026)

Proven by a change in a save: 20, 22, 24, 26, 32-47, 50-60 (all but the gaps), 63. The rest (0-19, 28-31) by the row rule,
which every proven bit follows. 48, 49, 53, 61, 62: no trait in the editor (hidden or unused); keep them unnamed.

| Bit | Trait | Bit | Trait |
|---|---|---|---|
| 0 | Runs With Ball Down Left | 1 | Runs With Ball Down Right |
| 2 | Runs With Ball Through Centre | 3 | Gets Into Opposition Area |
| 4 | Moves Into Channels | 5 | Gets Forward Whenever Possible |
| 6 | Plays Short Simple Passes | 7 | Tries Killer Balls Often |
| 8 | Shoots From Distance | 9 | Shoots With Power |
| 10 | Places Shots | 11 | Curls Ball |
| 12 | Likes To Round Keeper | 13 | Likes To Try To Beat Offside Trap |
| 14 | Uses Outside Of Foot | 15 | Marks Opponent Tightly |
| 16 | Winds Up Opponents | 17 | Argues With Officials |
| 18 | Plays With Back To Goal | 19 | Comes Deep To Get Ball |
| 20 | Plays One-Twos | 21 | Likes To Lob Keeper |
| 22 | Dictates Tempo | 23 | Attempts Overhead Kicks |
| 24 | Looks For Pass Rather Than Attempting To Score | 25 | Plays No Through Balls |
| 26 | Stops Play | 27 | Knocks Ball Past Opponent |
| 28 | Moves Ball To Right Foot Before Dribble Attempt | 29 | Moves Ball To Left Foot Before Dribble Attempt |
| 30 | Dwells On Ball | 31 | Arrives Late In Opponents' Area |
| 32 | Tries To Play Way Out Of Trouble | 33 | Stays Back At All Times |
| 34 | Avoids Using Weaker Foot | 35 | Tries Tricks |
| 36 | Tries Long Range Free Kicks | 37 | Dives Into Tackles |
| 38 | Does Not Dive Into Tackles | 39 | Cuts Inside From Both Wings |
| 40 | Hugs Line | 41 | Gets Crowd Going |
| 42 | Tries First Time Shots | 43 | Tries Long Range Passes |
| 44 | Likes Ball Played Into Feet | 45 | Hits Free Kicks With Power |
| 46 | Likes To Beat Opponent Repeatedly | 47 | Likes To Switch Ball To Wide Areas |
| 48 | (hidden) | 49 | (hidden) |
| 50 | Possesses Long Flat Throw | 51 | Runs With Ball Often |
| 52 | Runs With Ball Rarely | 53 | (hidden) |
| 54 | Does not Move Into Channels | 55 | Uses Long Throw To Start Counter Attacks |
| 56 | Refrains From Taking Long Shots | 57 | Cuts Inside From Left Wing |
| 58 | Cuts Inside From Right Wing | 59 | Crosses Early |
| 60 | Brings Ball Out Of Defence | 61 | (hidden) |
| 62 | (hidden) | 63 | Plays Ball With Feet |

To read: the u64 at the birth date - 27; drop a record with more than about 12 bits (a misaligned read, about 1%).
The trait names are in the game's compressed archives (`data/*.fmf`), so their internal order was found this way, not read
from a file.
