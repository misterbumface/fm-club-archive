"""Write one article from one story lead, in one outlet's voice (docs/media-plan.md, step 3).

The notebook is the lead's own facts and the background of the people and clubs it is about,
read from the world database. The model chosen in Settings writes a headline, a standfirst and
the body; every year and score in them must be in the notebook (story_writer.text_faults). One
wrong draft gets one more try with its faults listed; a second wrong draft is refused.
"""

from __future__ import annotations

import json
import random
import re
import time
from collections import Counter
from datetime import date

from flask import current_app
from sqlalchemy.engine import Connection

from app.extensions import db
from app.models import world as world_models
from app.models.outlet import Outlet
from app.services import moods, story_notebooks, world_queries as q
from app.services.ai_settings import get_checker_model, get_papers_model, resolve_ai_api_key
from app.services import archive_questions, fact_check
from app.services.story_leads import Lead
from app.services.story_writer import ARTICLE_RULES, StoryError, hard_faults, request_json, request_with_questions, text_faults

# How long an article is, by its place in the edition.
PROMINENCE = {
    "lead": "the front-page lead: 450-700 words",
    "standard": "a standard article: 250-400 words",
    "brief": "a brief: 60-120 words, one or two paragraphs",
    "feature": "a long feature: 700-1000 words, with two to four short section headings, each on its own line",
    "column": "a long column: 1100-1500 words, with three to five short section headings, each on its own line",
    "matchday": "a long match-day article: 800-1200 words, with a short section heading on its own line for each big game or group of games",
}

# A column's opening, whatever the edition's deal of forms.
COLUMN_OPENINGS = {"travel_log": "Open as this_trip's the_shape says (in the notebook's story facts), never as the_last_columns opened."}

# Each article of an edition opens and is headlined in its own way, so an edition does not read as one formula.
OPENINGS = (
    "Open on one vivid moment of the story, told as a scene.",
    "Open with the single number that matters most.",
    "Open with a short quote from someone in the story.",
    "Open with what was at stake before it happened.",
    "Open wide, on the bigger picture, then narrow to the story.",
    "Open with a sharp contrast: then and now, them and us, expected and real.",
    "Open with a question that the article answers.",
    "Open with the verdict, stated boldly, then earn it.",
)
HEADLINES = (
    "a pun or a play on words",
    "plain and punchy, led by a strong verb",
    "a question",
    "a short quote from the story",
    "very short: three to six words",
    "built on a number",
    "a bold verdict",
)
TEMPERATURE = 0.8  # more freedom than the archive's own texts
# Extra direction for some kinds of story.
KIND_NOTES = {
    "legend_profile": "This is a retrospective of a great career, not news. When still_playing is false, he stopped playing "
                      "long ago (stopped_playing_after, years_since_his_last_season): write it as history, looking back, and "
                      "never as a retirement, a farewell or a last game happening now.",
    "career_end": "This is news: his playing career has just ended, after the season named in the facts.",
    "where_now": "This is a 'where are they now' feature: a great player of the past and the job he does today. Look back "
                 "on the playing days and bring the reader to the present.",
    "appointment": "This is news: a former player has just been appointed to a staff job.",
    "scouting": "This is a scouting report: your scouts have watched him closely. Describe what they saw and his standout "
                "traits in a scout's language, with concrete moments rather than labels: the moments come only from the games in "
                "his_games_this_season, with his minutes, goals and assists as given there; a game not listed, or one he sat "
                "out, holds no moment of his, and a team's result is never his performance. With no games listed, write of "
                "his qualities, not of games. Nobody knows how good a player will "
                "become: you may guess, as scouts and journalists do, but never state his future as a fact, and never give "
                "ratings or numbers for his ability. scout_view is one scout's private note: build on it in your own words, "
                "never quote it, and do not compare him with 'his age group'.",
    "performance": "This is about one player's display in one match: put him at the centre and tell the match through what he "
                   "did, using the game's match report (match_report) for the moments and the numbers that matter.",
    "sending_off": "This is about a red card in a big match: the moment, what it cost his side and how the match turned "
                   "after it. Use the game's match report (match_report) for the minute and what followed.",
    "smash_and_grab": "This is a win against the run of play: the beaten side had the ball, the shots and the better chances "
                      "(expected goals), the winners took theirs. Tell how, with a few of the match report's numbers; do not "
                      "explain what expected goals are.",
    "league_round": "This is the match-day article: one piece on a league's round of games, in place of many small reports. "
                    "Tell the big_games one by one, the most important first, each with its goals, its player of the match and "
                    "what it did to the places, as a reporter who saw the whole round; the stories_of_the_day (a big performance, "
                    "a sending-off, a win against the run of play) belong to their games. Then the other results in a sentence or "
                    "two, naming only the notable, and what the round did to the table: the leaders, who climbed and who fell. "
                    "Report the present, not history.",
    "misfiring": "This is about a player who is not finishing his chances: his league goals are far below the expected goals "
                 "of the chances he has had. Tell it with the numbers given (goals, expected goals, shots, how long since his "
                 "last goal), what it costs his club, and that the chances keep coming, which is the hopeful part. When "
                 "of_them_penalties is given, the penalties are in both his goals and his expected goals. Do not explain what "
                 "expected goals are.",
    "clinical": "This is about a player finishing far above his chances: his league goals are far above the expected goals of "
                "the chances he has had. Tell it with the numbers given, as a hot streak and a question: skill, or luck that "
                "will run out. When of_them_penalties is given, the penalties count in both. Do not explain what expected goals are.",
    "tired_legs": "This is about a player worn down by the games. In the game's terms this is fatigue, the tiredness that builds "
                  "over weeks of matches and that only a real rest clears, not the short tiredness after one game that a day or "
                  "two cures. Tell it from his_state, the games he has played this month, his ratings of late against the "
                  "season's, and the games to come, and put the manager's choice: rest him or risk him. Do not invent an injury "
                  "or a medical opinion.",
    "run": "This is about a club's run of form now: the run, how it was built (the recent results) and where it leaves them "
           "in the table. When pressure_on_the_manager is in the facts, the manager's position is part of the story; say he "
           "is under pressure, never that he is sacked or will be.",
    "derby": "This is a derby report: the result, the goals and what it means between two clubs that count each other "
             "rivals. The earlier meetings give the recent history of the fixture; do not invent older history.",
    "transfer_interest": "Clubs are after a player: who, and what it means for his club. No wage or fee is known at this stage "
                         "(a contract is negotiated only after a bid is accepted): name none. The interest "
                         "is from the game's own lists; a rumoured club has not acted. The clubs after him are the story and the headline; "
                         "a club that has him among dozens on its list ('also on the lists of', with the list's size) is a footnote "
                         "at most, never the headline, never 'circling'. Say the window status if known; never invent an offer, an agent's words or the player's wish.",
    "transfer_bid": "A bid has been made, agreed, completed or has collapsed, with its fee and dates. Tell it as a market story: "
                    "the money, the club's need, the player's position; an agreed deal names when he joins; a collapsed one says "
                    "it fell through without inventing why.",
    "promised_report": "This is the report of a game this paper previewed: the promise kept. Tell the result, the goals and "
                       "what it means, and settle what the preview said (what_we_said: the expected shapes, the key duels, who "
                       "was missing, what each side needed) against what happened, in a sentence or two, without quoting it at "
                       "length; refer to it as our preview, never mock it. Choose the length: a full report when the facts carry "
                       "a story (goals, cards, an upset, a run), about 250 words when the game was ordinary.",
    "preview": "This is a preview of a game still to be played, both sides' next game in its competition: why it matters (the "
               "table, the form, a derby), who is missing and how the last meetings went. The piece is about this game; later "
               "games may be named only as what lies ahead. Never give the result or write as if it has been played; you may say what "
               "each side needs. The expected line-ups are each side's usual system and usual players without the injured: "
               "a 'system on the tactics screen' fact is that side's actual set-up (shape, style, mentality, each role) and "
               "may be used as fact. "
               "say 'expected' or 'likely', never that the team has been announced. The key duels set a winger against the "
               "full-back facing him, with their average ratings in recent games. A record with a formation (home_record_with_their_...) is "
               "that side's results over the last year when it played its expected formation; a record against a formation "
               "(home_record_against_a_...) its results against teams playing the formation the other side is expected to use. "
               "attribute_duels are the game's own attributes (1-20) of players who are expected to meet: a winger against "
               "the full-back facing him, a striker against the centre-backs in the air or in behind. Use one or two as the "
               "battles to watch; the numbers are true, the outcome is not known. A system against stronger (or weaker) sides is "
               "the shape that side has used against such opponents over the last year, when it differs from its usual one: "
               "the expected line-up is in that shape. It is a habit, not an announcement. danger_men, in_form and out_of_form "
               "come from each side's last five matches with a report (ratings oldest first): name the threat and the form. "
               "last_meeting_with_team_sheets is the last time they met: the shapes, the elevens, the goals, and who from "
               "that day's eleven is not in the expected eleven now (and why, when known) and who is new. home_fitness and "
               "away_fitness are the game's own condition, match sharpness and fatigue of expected starters who are not fully "
               "fit, short of sharpness or tired, on the date given: a doubt or a tired legs story, not an injury. home_expected_goals_and_points and away_... "
               "compare each side's league points with the points its chances deserved (from xG): a side far above may be "
               "riding its luck or finishing well, one far below wasting chances.",
    "injury_blow": "This is news: a player has just been injured and will be out for weeks. Say what he has, how long he is "
                   "expected to be out and what his club loses (his position, the games he misses). The return date is the "
                   "medical staff's estimate, not a certainty.",
    "underperformer": "This is about a big name below his level this season: his league numbers and his average rating "
                      "after a few games. Report it as the season so far, fair to him; do not predict he will stay poor.",
    "overperformer": "This is about a little-known player doing far better than his name: his league numbers and his "
                     "average rating this season. Report it as the season so far; do not predict he will stay this good.",
    "expectations": "This is about a club far from where its standing in the league suggests: the table place against "
                    "the place its standing suggests, the points and the form. Report the season so far; it is early "
                    "when few games are played.",
    "check_in": "This is a check-in on a club in an unusual place: a big club that went down, or a small club that came up. "
                "Tell how their season is going from the notebook: the place, the points, the form, last season, the manager, "
                "the scorers and the regulars. It is a mid-season look, not a verdict; promotion, play-off or relegation places "
                "only as the notebook's league_rules give them.",
    "stat_watch": "This is about a scoring pace: the league's top scorer, his goals and games this season and who chases "
                  "him. Report it as the season so far.",
    "system_change": "This is about a team that has changed its system: the usual shape, the new one, the results in each and "
                     "a new manager if there is one. Only the shapes are known (who played where), not the instructions: do not "
                     "invent pressing, roles or tactics beyond them.",
    "position_change": "This is about a player moved to a new role: where he used to play, where he plays now and how he does "
                       "there (starts, average rating, goals and assists in each). Do not invent why he was moved.",
    "dropped": "This is about a regular who has lost his place: he was on the bench, no injury is recorded, and someone else "
               "plays in his slot (a new signing, a youngster). Report it as a fact of the team sheets; do not invent a "
               "quarrel, a reason or what the manager said.",
    "breakthrough": "This is about a youngster who has broken into the team: his age, his slot, his starts and ratings, and "
                    "whose place he has taken.",
    "partnership": "This is about a pair who play together and the team's record with them and without one of them; the "
                   "verdict (strong or struggling) comes from those numbers. Report it as the season so far.",
    "tactical_trend": "This is about a league's systems this season: how often each shape is used and the points a game it "
                      "wins, and which clubs use which. Only the shapes are known, not instructions.",
    "unsettled": "This is about an unsettled player: the concern the game records (he wants to move on, a new deal, to go "
                 "home, more games, or was left out of a squad), since when, and whether he has told his team-mates. Say only "
                 "the stage the facts give: a concern he keeps to himself is not public; told to his team-mates is dressing-room "
                 "talk; team-mates who think he is treated unfairly have taken his side. Do not invent an agent, a transfer "
                 "request, a quarrel with the manager or what anyone said in public.",
    "dressing_room": "This is about a dressing room taking sides: the team-mates who think a player has been treated unfairly, "
                     "whether they come from his own circle or other circles, and his own grievance. It is dressing-room talk, "
                     "not a public row: do not invent statements, a revolt, a transfer request or the manager's answer.",
    "settled": "This is about a player's concern that has ended: what it was and how long it lasted. The save does not say "
               "what ended it: do not invent a talk with the manager, a new contract or a promise.",
    "tie_report": "This is the report of one knockout game of a big continental cup: the round and the leg, the score and "
                  "the goals in order, then the tie: the first leg, the aggregate, who goes through and how, and what they "
                  "reach. Say only what the tie facts give: no draw for the next round, no opponent they do not name.",
    "knockout_round": "This is the knockout round piece: one long article on a big continental cup's knockout games since the "
                      "last editions, every game in it. Open with the biggest story (a giant out, a comeback, a rout), then "
                      "give each game its own paragraph, in the games' order: the score, the goals in order and how they went "
                      "in, and the tie (the first leg, the aggregate, who goes through and how). Say only what each game's "
                      "facts give: no draw for the next round, no opponent they do not name.",
    "knockout_preview": "This is the preview of a big continental cup's coming knockout games, all in one piece: open with the "
                        "tie that matters most, then give each tie its own paragraph: the round and the leg, the tie as it "
                        "stands, the form, who is missing, the last meetings. Never give a result or write as if a game has "
                        "been played. ",
    "tie_preview": "This is the preview of one knockout game of a big continental cup: the round and the leg, the tie as it "
                   "stands (the first leg, the aggregate), what the winner reaches, the form, who is missing, the last "
                   "meetings. Never give the result or write as if it has been played. The piece is about this game. ",
    "continental_round": "This is the round-up of a continental competition's games since the last editions (the region fact "
                         "says which part of the world: never call a South American cup European): the results, the "
                         "biggest clubs' first, and the groups or the league phase as they stand; in the knockout rounds, "
                         "each tie as the results give it. Lead with the biggest story "
                         "of the round (a giant beaten, a club through or out, a rout), then the rest; say what the table means "
                         "for the next matchday. Report only the results and tables given.",
    "european_nights": "This is about one country's clubs in continental competition since the last editions (the region fact "
                       "says where: Europe, or South America for the Libertadores; never call it Europe otherwise): every "
                       "result in every continental competition and where they stand in their groups. Write it for that country's readers: how their "
                       "clubs fared, who flew the flag and who let it down, and what comes next. Report only the results given.",
    "travel_log": "This is the Away Days column: a first-person travel log of one trip to one game, in the year the notebook gives. "
                  "It is a long piece with meat on it, never shorter than 1,100 words whatever the notebook holds (the town and the "
                  "real club's history come from your own knowledge), with three to five short section headings, each a few words on "
                  "a line of its own; the game is the smallest part. this_trip says how this trip is told, and it rules the piece: "
                  "the_angle leads it, the_shape sets how it opens and runs, what_happens is what the day brings (life on the road: "
                  "tell it as it happened to him, in scenes, and let it change his day), the_game_is the kind of day out, and "
                  "running_thread, when given, comes back once, lightly. The things a trip can hold, for the angle to choose among and "
                  "weigh, never a checklist in a fixed order: the journey (the route from the last stop over the real geography, "
                  "trains, buses, ferries, roads, hours, changes, borders, rough cost; a paragraph or less unless the angle is the "
                  "journey). The town: who lives there and what they do, what it was built on, made and lost, its real landmarks "
                  "visited (the cathedral, the castle, the bridge, the old quarter, the museum, the market), and what it is like to "
                  "arrive. The club, always, in two layers: first the real club's history before our day, as a well-read traveller "
                  "knows it (when and how it was founded, its nickname and colours, its old grounds, its famous days and long years, "
                  "its place in its town); then the notebook's decades (the_home_club; its honours are the whole record, the oldest under today's competition names, so a 1912 title is the league of that level then): "
                  "the climbs and falls between the divisions, what it won, its legends, rivals and longest servants, its manager, where "
                  "it stands now, told as the story of the club and not as a list. The people met, in character, with what they said. "
                  "The food by its proper name, when the day has room for it, not by habit. The ground from where he stands or sits. "
                  "Then the game as the goals say (how_the_goals_went_in tells each goal; the build-up is not known, so tell none), as "
                  "he saw or heard it, at most a quarter of the piece, set against the club's story (the_visitors and meetings_before "
                  "give the other side). the_last_columns are the column's last pieces: do not reuse their headlines' wording, their "
                  "openings or their section headings, and do not bring back their stock figures (an old man in a flat cap, a boy in "
                  "the club shirt, the walk back to the station, the price of every meal). Whatever happens on the day, the match facts "
                  "stand as the notebook gives: the score, the scorers and minutes, the crowd. Name only what you know of the real "
                  "place and the real club; where you do not know a particular, say less, and let the weather, the landscape and the "
                  "people met carry the paragraph. Particular things only: no 'passionate fans', no 'hidden gem', no 'football is life "
                  "here', no list of generic charms. Mention an earlier stop when it fits. It is the notebook's year, decades on from "
                  "our day: no event, person or price of the 2020s; history older than that is fair. The match facts and the club's "
                  "archive years are the notebook's and nothing more.",
    "interview": "This is a question-and-answer interview with the person named, on the hook given. Six to ten questions in the "
                 "interviewer's voice, his answers in character and in the first person: his memories of the clubs, seasons, "
                 "honours and awards the notebook lists, what he does now, what he thinks of the game. His opinions are free; his "
                 "facts are the notebook's and nothing more: no game, goal, team-mate, manager, fee or date the notebook lacks. "
                 "His character and the people in his life (people_in_his_life: whom he is close to, whom he dislikes, who "
                 "backs him) are the interview's spine: ask about them by name, let him answer as the man he is, and let a "
                 "grudge or a debt show. Set the scene in two sentences first.",
    "under_pressure": "The press reports that a club's board are losing patience with the manager and that a coming game "
                      "could decide his fate. It is a report, not a sacking: say so. The results given show why.",
    "national_squad": "This is about a national team's new squad: who is in, who is left out, and the manager's thinking. It is the "
                      "squad for a tournament only when the_finals_squad_for says so; when games_played_since_the_last_squad is given, it "
                      "is the squad for those games, so tell it as past. Lead with "
                      "the biggest name left out or brought in. The squad, the caps, the starts, the injuries and the club form are "
                      "facts: tell them as given. A left-out player's why is given only when it says so; when no_reason_known is there, "
                      "say the manager has given none. You may speculate on the shape and the strategy from the_shape (the squad's lines "
                      "before and now, the usual shape, the manager's preferred one), but say it is the paper's guess. 'Uncapped' is a "
                      "fact; never call anyone's call-up his first unless told so. No squad was announced at a press conference: no quotes "
                      "from the manager on it.",
    "club_accounts": "This is about a great club's money, told when its accounts come out. the_story says which way it goes. No paper sees a private club's "
                     "books: every figure is rounded and comes from the accounts or people close to the club, so write about, reported or believed, and "
                     "never make a figure more exact. Say what the sums mean: wages, debt and an overdraft like this cannot go on, or a pile of cash like this "
                     "is what rivals dream of. What may come next is the paper's guess, and say so: a forced sale, a takeover, administration and points "
                     "docked. None of it has happened unless told; invent no owner, sale, bid, loan or statement. Quotes only from unnamed people close to the club, "
                     "and never with a figure in them.",
    "points_docked": "This is about a club with points taken off in the league. points_taken_off is a fact. The reason is not known unless given; when "
                     "their_money shows the club deep in the red, the paper may suggest that money is the reason and say it is suggesting. Its money figures are "
                     "rounded and reported: say about or believed. Invent no ruling, date, hearing or statement.",
    "squad_dropped": "This is about one well-known international left out of his country's squad; it is a tournament squad only when "
                     "the_finals_squad_for says so. His caps and his starts show what "
                     "he has been to the team; his why is given only when it says so, and no_reason_known means nobody has said. "
                     "likely_replacements are the new names in his part of the team: the paper's guess at who takes his place, so say "
                     "so. Imagine how he takes it from his character and how he gets on with the manager (when given): colour, in "
                     "his own manner, never a fact or a number of your own. the_shape allows a guess at a change of plan, as a guess.",
    "goal_award": "This is about a goal award (Goal of the Month and the like): the winning goal first, then the others in "
                  "order, each in its game. Tell how each went in only as the_goals_in_order says (the kind of shot, the range, "
                  "the height); no distances in metres, no build-up, no corner side: they are not recorded.",
    "crisis": "This is about a well-known club or nation in crisis: a squad this good (the_squad: its best players) taking "
              "far fewer points than it should. The results given are the crisis; tell them as they are, with the players who "
              "should be winning these games, the manager and the board when given, and the mood. the_story says whether it "
              "begins or goes on. Never print the papers' own reading as a number of its own: no ratings, no expected points.",
    "crisis_over": "This is about a well-known club or nation whose crisis is over: the games that ended it, and how the "
                   "results have caught up with a squad this good. Tell the bad run before only as the results given.",
    "vacancy": "A club without a manager, and the men the press link with the job (some have denied it or said nothing). "
               "No appointment is known: never name one.",
    "transfer_rumour": "A press report that a club is after a player, with the fee reported. No bid is known: say it is a "
                       "report. The fee is as reported, rounded.",
    "loan_offer": "A club asked to borrow a player on loan. Say who asked, when, and the outcome only as given; no fee "
                  "exists for a loan, and none of the player's value is a fee.",
    "war_of_words": "Two managers have traded words at press conferences; the exchanges are the game's own headlines for "
                    "their answers, with the date, the match and the tone. Tell the feud: who started it, how it escalated, what "
                    "the result between them was, what is next. Paraphrase the spirit of each answer in your own words; quote no "
                    "line that is not a headline given, and invent no further exchange.",
    "kings_wage": "A player the followed club sold is paid a king's wage abroad: his wage now (as given, rounded as the game "
                  "shows it), the deal that took him, his years at the club, and what the club's best-paid player earns for "
                  "contrast. Lively, but report only the figures given; do not invent bonuses, taxes or quotes.",
    "contract_running_down": "A regular's deal at the followed club runs out within a year: his wage, the end date, his age, his "
                             "season and his mood (from the game). Say what the club risks and what it might do, without inventing "
                             "talks, offers or the player's words.",
    "manager_out": "A high-profile club's manager has gone: how (sacked, resigned, retired, as given), his spell's record and "
                   "honours, where the club stands, and the successor when one is named. Do not invent the board's words, a "
                   "pay-off or candidates.",
    "scouting_follow_up": "This paper scouted this player a year ago (or two years ago: years_since_our_report), and looks him up "
                          "again: what we said then (what_we_said) and what has happened since (his moves, his seasons, his injuries, "
                          "the level a scout sees now). Settle our call honestly: right, early, or wrong, and say why, from the "
                          "facts given only. Refer to it as our report. Do not invent quotes, fees or games. Add to your JSON "
                          "answer a field \"verdict\" with one word, \"right\", \"early\" or \"wrong\": the paper's call on its own report.",
    "club_report": "This is the report of one game of the club this paper follows, told as of that game's day: its other "
                   "games, before or after, have pieces of their own, so never lead with one or put it in the headline. The "
                   "result, the goals, the "
                   "player of the match, what it means in the table. When what_we_said is there, settle what our preview said "
                   "against what happened in a sentence or two, as our preview, never mocked. Choose the length: a full "
                   "report when the facts carry a story, about 250 words when the game was ordinary, and a brief (60-120 words) "
                   "when the treatment says so (a friendly). Report only what is given.",
    "club_preview": "This is the preview of the next game in its competition of the club this paper follows, for its readers: "
                    "why it matters, the table, the form (the league_season facts are the whole record: no other defeat or draw), "
                    "who is missing, the last meetings, and what the drama facts say. The piece is about this game; later games "
                    "may be named only as what lies ahead. Never give the "
                    "result or write as if it has been played. A brief (60-120 words) when the treatment says so (a friendly).",
    "tournament_report": "This is the report of one game of a major international tournament (the World Cup, a continental "
                         "championship): the round, the score, the goals, the player of the match, what it means in the group or "
                         "the bracket, and our clubs' players who were in it. When the notebook has the full match report, use it; "
                         "when it has only 'players we know of', those are the only players you may describe. Report only what is given.",
    "tournament_preview": "This is a preview of a game to come in a major international tournament: the round, what both sides "
                          "have done in the tournament so far, the group as it stands, our clubs' players in the two squads, and "
                          "the last meetings. Do not invent line-ups, injuries or quotes.",
    "tournament_round_up": "This is the round-up of a major international tournament since the last editions: where it stands, "
                           "the results of the cycle, the groups, the top scorers, the games to come and how our clubs' players "
                           "have done. Lead with the biggest story. Report only the results and tables given.",
    "our_players_at_tournament": "This is about the followed clubs' players at a major international tournament: what each did "
                                 "in the games of the cycle (minutes, goals, assists, rating) and so far, and their nations' next "
                                 "games. Write it for the club's readers. Report only what is given.",
    "player_of_the_tournament": "This is the piece after the final of a major international tournament: who won it and how, the "
                                "best-rated players over the tournament, the top scorers, and how our clubs' players did. Name a "
                                "player of the tournament from the ratings and goals given, and say why. Report only what is given.",
    "international_round": "This is the round-up of a national teams' competition since the last editions (World Cup or "
                           "European Championship qualifiers, a Nations League, a tournament): the results, the biggest nations "
                           "first, the groups as they stand and the scorers. Lead with the biggest story (a giant held, a nation "
                           "through, a rout) and say what the groups mean. Report only the results and tables given.",
    "national_team": "This is about one country's national team since the last editions: its results, its scorers, where it "
                     "stands in its group and its next games. Write it for that country's readers. Report only what is given; "
                     "do not invent the manager's words or a squad announcement.",
    "expected_points": "This is about a team whose points are far from what its chances deserve: expected points come from the "
                       "quality of the chances each side had in each game (xG). Above expected can be clinical finishing, a "
                       "goalkeeper in form or luck; below can be wasted chances or a leaky defence. The finishing and defending "
                       "facts say which. Report it as the season so far; do not invent individual chances.",
    "transfer": "This is transfer news. When the facts have same_position_players_who_left, same_position_injured_now or "
                "competing_for_the_place, say where he fits: whom he replaces, who is out, and whom he competes with.",
}

# The writer's picture (27 Oct 2026): the one who wrote the piece knows its place and moment best; the fixed scenes per kind
# gave every story of a kind the same stadium. Picture models draw names as strangers, text and numbers as nonsense.
PICTURE_RULE = ("The picture is one or two sentences for a photographer: the single moment from your piece that a press photograph "
                "of it would show, only what a camera sees: the place, the people by their role and look (a goalkeeper in amber, "
                "an old man in a flat cap), the weather and light, the colours. No names of people, clubs or grounds, no "
                "numbers, scores, words, signs, crests or logos, and no feelings or story.")
PICTURE_MAX = 500

PROMPT = (
    "You are {writer}, writing for {name}{tagline}. {persona}The outlet's voice: {voice} Write {length} about the story in "
    "the supplied notebook, published on {published}, in your own way. The notebook's dates are when things happened, and "
    "the paper comes out on {published}: tell each game and event on its own day ('on Monday', 'on 29 December', 'three days "
    "ago'), and use 'today', 'tonight' or 'this afternoon' only for what happened on {published} itself. {opening} The headline: {headline_form}. Do not open "
    "the standfirst or the article with a player's age ('At just 19…'), and do not use the 'Name: description' headline "
    "pattern. Build the article around the story's angle (the notebook's story.headline says what the "
    "news is); use the background on people, clubs and the competition only where it serves the story. Never refer to 'the "
    "record', 'recorded here' or where the facts come from. A club's title_runs say which runs of titles go on and which "
    "ended, and how: for any run, use them, never the background. When the notebook has few facts, write shorter than the range; "
    "never pad with repetition or minor results. " + ARTICLE_RULES + " Write in British "
    "English, in your voice and the outlet's. Return a JSON object {{\"headline\": \"...\", \"standfirst\": \"...\", \"body\": "
    "\"...\", \"picture\": \"...\"}}: a headline in the outlet's style of at most 14 words, a standfirst of one sentence, the body "
    "as plain paragraphs separated by blank lines, with no Markdown, bullets, byline or labels, and the picture. " + PICTURE_RULE
)


def lead_to_dict(lead: Lead) -> dict:
    return {"kind": lead.kind, "headline": lead.headline, "date": lead.date.isoformat(), "score": lead.score,
            "facts": lead.facts, "refs": lead.refs, "beats": sorted(lead.beats), "slug": lead.slug}


def lead_from_dict(data: dict) -> Lead:
    return Lead(data["kind"], data["headline"], date.fromisoformat(data["date"]), float(data["score"]), data.get("facts") or {},
                list(data.get("refs") or []), set(data.get("beats") or []), data.get("slug"))


def article_notebook(connection: Connection, lead: Lead, published: date) -> dict:
    """The lead's facts and the background of the people and clubs it names."""
    notebook: dict = {"published_on": published.isoformat(),
                      "story": {"kind": lead.kind.replace("_", " "), "headline": lead.headline, "date": lead.date.isoformat(),
                                "facts": lead.facts}}
    people, clubs = [], []
    for ref in lead.refs:
        kind, _, rest = ref.partition(":")
        if kind == "edition" and "competition_season" not in notebook:
            competition_uid, _, year = rest.partition(":")
            edition = _edition(connection, int(competition_uid), int(year))
            if edition:
                notebook["competition_season"] = edition
        elif kind == "match" and "match_report" not in notebook:
            day, home, away = rest.split(":")
            fixture = q._one(connection, "select id from fixtures where date = :d and home_uid = :h and away_uid = :a",
                             d=day, h=int(home), a=int(away))
            report = story_notebooks.match_report_facts(connection, fixture["id"]) if fixture else None
            if report:
                notebook["match_report"] = report
            elif fixture and "players_we_know_of" not in notebook:  # the report is gone: the rows kept for the followed players
                known = story_notebooks.known_players_facts(connection, fixture["id"])
                if known:
                    notebook["players_we_know_of"] = known
        elif kind == "person":
            person = _person(connection, int(rest), published)
            if person:
                people.append(person)
        elif kind == "club":
            club = _club(connection, int(rest), published)
            if club:
                clubs.append(club)
    if people:
        notebook["people"] = people
    if clubs:
        notebook["clubs"] = clubs
    sides = _sides(connection, lead)
    if len(sides) >= 2:
        # a transfer's own player is the story, not a connection: listed with his years at the selling club, he read
        # as a returning old boy ("Gérson comes home" to a club he had never played for)
        mover = {int(ref.split(":")[1]) for ref in lead.refs if ref.startswith("person:")} if lead.kind == "transfer" else set()
        links = _connections(connection, sides[0], sides[1], mover) + _connections(connection, sides[1], sides[0], mover)
        if links:
            notebook["connections"] = links
    return _for_writer(notebook)


# Facts the finder needs for ranking but a writer turns into odd prose ("after 159 editions").
_NOT_FOR_WRITERS = ("editions_played_before",)


CONNECTIONS = 8  # players with a past at the other club, at most
CLUB_BACKGROUND_LIMIT = 1200  # characters of a club's background text in an article's notebook


def _sides(connection: Connection, lead: Lead) -> list[int]:
    """The two clubs (or national teams) of a story: a final's or a title's first two, a match's sides, or its club refs."""
    sides: list[int] = []
    for ref in lead.refs:
        kind, _, rest = ref.partition(":")
        parts = rest.split(":")
        if kind == "edition":
            sides += [r["team_uid"] for r in q._rows(connection, "select team_uid from edition_places where competition_uid = :c "
                                                                 "and year = :y and place <= 2 order by place",
                                                     c=int(parts[0]), y=int(parts[1]))]
        elif kind == "match":
            sides += [int(parts[1]), int(parts[2])]
        elif kind == "club":
            sides.append(int(parts[0]))
    return list(dict.fromkeys(uid for uid in sides if uid))


def _connections(connection: Connection, club: int, other: int, leave_out: set[int] = frozenset()) -> list[dict]:
    """Players now at one club who once played for the other: when, how much, and what they won at that other club.
    Every field names the club it is about: a field called "there" was read as the player's new club."""
    names = q.teams(connection, [club, other])
    rows = q._rows(connection, "select p.fm_uid, p.name, min(ps.season_start) first, max(ps.season_start) last, "
                               "sum(ps.starts + ps.substitute_appearances) games, sum(ps.goals) goals from people p "
                               "join teams now on now.fm_uid = p.team_uid join player_seasons ps on ps.person_uid = p.fm_uid "
                               "join teams was on was.fm_uid = ps.team_uid where coalesce(now.club_uid, now.fm_uid) = :club "
                               "and coalesce(was.club_uid, was.fm_uid) = :other and ps.kind in (1, 2, 3) group by p.fm_uid "
                               "having games > 0 order by games desc limit :n", club=club, other=other, n=CONNECTIONS)
    found = []
    for r in rows:
        if r["fm_uid"] in leave_out:
            continue
        won = [h for h in q.player_honours(connection, r["fm_uid"]) if h["place"] == 1 and h["team_uid"] == other]
        found.append({"player": r["name"], "now_at": names.get(club, {}).get("name"),
                      "formerly_at": names.get(other, {}).get("name"),
                      "seasons_at_former_club": f"{q.season_label(r['first'])} to {q.season_label(r['last'])}",
                      "games_at_former_club": r["games"], "goals_at_former_club": r["goals"],
                      "trophies_won_at_former_club": len(won)})
    return found


def _for_writer(value):
    """Facts as a writer should see them: fees as a newspaper writes them (fee_pounds 41728360 becomes
    fee "£41.7m"); a competition's "edition" called its season; no euro copies, no reputation numbers,
    no "run" of one."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if (key.endswith(("_euros", "_reputation")) or key in _NOT_FOR_WRITERS
                    or (key == "titles_in_a_row" and (item or 0) < 2)):
                continue
            if key.endswith("_pounds") and isinstance(item, (int, float)):
                out[key.removesuffix("_pounds")] = f"£{item / 1e6:.1f}m" if item >= 1e6 else f"£{round(item / 1e3)}k"
            else:
                out[key.replace("edition", "season")] = _for_writer(item)
        return out
    if isinstance(value, list):
        return [_for_writer(item) for item in value]
    return value


EDITION_KEYS = ("status", "winner", "runner_up", "previous_winner", "top_scorers")


def _edition(connection: Connection, competition_uid: int, year: int) -> dict | None:
    """The competition edition behind a title: the final and its scorers, the leaders, the top scorers."""
    edition = story_notebooks.edition_notebook(connection, competition_uid, year)
    if edition is None:
        return None
    row = {key: edition[key] for key in EDITION_KEYS if edition.get(key)}
    if edition.get("final"):
        row["final"] = _winner_side(edition["final"], edition.get("winner"))
        if row["final"].get("penalties"):
            kicks = _shoot_out(connection, competition_uid, row["final"]["date"])
            if kicks:
                row["final"]["shoot_out_in_order"] = kicks
        final = q._one(connection, "select id from fixtures where competition_uid = :c and date = :d and home_goals is not null",
                       c=competition_uid, d=row["final"]["date"])
        report = story_notebooks.match_report_facts(connection, final["id"]) if final else None
        if report:
            row["final"]["match_report"] = report
    if edition.get("table"):
        row["table_top"] = edition["table"][:4]
    if row.get("top_scorers"):
        row["top_scorers"] = [{k: v for k, v in s.items() if k != "penalties"} for s in row["top_scorers"][:3]]
    return row


def _shoot_out(connection: Connection, competition_uid: int, day: str) -> list[dict]:
    """The kicks of a final's penalty shoot-out, in order: team, taker, scored or missed."""
    final = q._one(connection, "select * from fixtures where competition_uid = :c and date = :d and home_penalties is not null",
                   c=competition_uid, d=day)
    if final is None:
        return []
    names = q.teams(connection, [final["home_uid"], final["away_uid"]])
    return [{"team": names.get(final["home_uid"] if g["side"] == 0 else final["away_uid"], {}).get("name"),
             "taker": g["name"], "scored": g["kind"] == 4}
            for g in q._rows(connection, "select g.side, g.kind, p.name from goals g left join people p on p.fm_uid = g.scorer_uid "
                                         "where g.fixture_id = :f and g.kind in (4, 5) order by g.seq", f=final["id"])]


def _winner_side(final: dict, winner: str | None) -> dict:
    """The final told from the winner's side: "Ponferrada beat Real Sociedad 1-0", so the order cannot go wrong."""
    if not final.get("score") or winner not in (final.get("home"), final.get("away")):
        return final
    home_goals, away_goals = final["score"].split("-")
    won_at_home = final["home"] == winner
    row = {"date": final["date"], "winner": winner, "beaten": final["away"] if won_at_home else final["home"],
           "score": f"{home_goals}-{away_goals}" if won_at_home else f"{away_goals}-{home_goals}"}
    if final.get("after_extra_time"):
        row["after_extra_time"] = True
    if final.get("penalties"):
        home_pens, away_pens = final["penalties"].split("-")
        row["penalties"] = f"{home_pens}-{away_pens}" if won_at_home else f"{away_pens}-{home_pens}"
    if final.get("scorers"):
        row["scorers"] = final["scorers"]
    return row


YOUTH_INTAKE = 14  # world_queries.TRANSFER_KINDS


def _person(connection: Connection, uid: int, published: date) -> dict | None:
    p = q._one(connection, "select p.*, n.adjective from people p left join nations n on n.fm_uid = p.nation_uid where p.fm_uid = :u", u=uid)
    if p is None:
        return None
    row: dict = {"name": p["name"]}
    age = q.age_on(p["birth_date"], published) if p["birth_date"] else None
    if age and age >= 15:
        row["age"] = age
    if p["adjective"]:
        row["nationality"] = p["adjective"]
    if p["team_uid"]:
        row["club_now"] = q.teams(connection, [p["team_uid"]]).get(p["team_uid"], {}).get("name")
    latest = q.latest_attributes(connection, uid) or {}
    character = q.character_words(latest.get("traits"))
    if character:  # the game's hidden traits in words: what the club and the scouts know of the man
        row["character"] = ", ".join(character)
    if latest.get("preferred_moves"):  # the game's player traits: his habits on the ball, never how one goal went in
        row["how_he_plays"] = ", ".join(latest["preferred_moves"]).lower()
    from app.services import moods

    people = moods.people_in_his_life(connection, uid)
    if people:  # whom he likes and dislikes, who likes and dislikes him, with the game's reasons
        row["people_in_his_life"] = people
    mind = moods.frame_of_mind(connection, uid)
    if mind:
        row["frame_of_mind"] = mind
    try:
        press = moods.press_words(connection, uid, published)
    except Exception:  # noqa: BLE001 - an archive imported before the press answers were read has no table yet
        press = None
    if press:  # the game's headlines for his press answers and for what others said of him: the spirit, never the words
        row["in_the_press"] = press
    news = moods.news_words(connection, uid, published)
    if news:  # the game's news about him: links with jobs, a board losing patience, pundits naming him, rumoured bids
        row["in_the_news"] = news
    career = q._one(connection, "select count(distinct case when kind = 1 then team_uid end) clubs, "
                                "sum(case when kind in (1, 2, 3) then starts + substitute_appearances else 0 end) games, "
                                "sum(case when kind in (1, 2, 3) then goals else 0 end) goals, "
                                "sum(case when kind = 4 then starts + substitute_appearances else 0 end) caps, "
                                "sum(case when kind = 4 then goals else 0 end) international_goals, "
                                "min(season_start) first, max(season_start) last from player_seasons where person_uid = :u", u=uid)
    if career and career["games"]:
        row["career"] = {"from": q.season_label(career["first"]), "to": q.season_label(career["last"]),
                                     "club_games": career["games"], "club_goals": career["goals"], "clubs": career["clubs"],
                                     "national_team_games": career["caps"], "national_team_goals": career["international_goals"]}
        # each club with its seasons, games and goals, so a writer cannot take a career at one club for one at another
        # (a signing from Bielefeld written up as "his fourth season in Chelsea's academy")
        spells = q._rows(connection, "select coalesce(t.club_uid, ps.team_uid) club, min(ps.season_start) first, max(ps.season_start) last, "
                                     "sum(ps.starts + ps.substitute_appearances) games, sum(ps.goals) goals from player_seasons ps "
                                     "join teams t on t.fm_uid = ps.team_uid where ps.person_uid = :u and ps.kind in (1, 2, 3) "
                                     "group by 1 having games > 0 order by last desc, first desc", u=uid)  # the latest first
        names = q.teams(connection, [s["club"] for s in spells])
        row["career_by_club"] = [f"{names.get(s['club'], {}).get('name', 'unknown club')} {q.season_label(s['first'])}"
                                 + (f" to {q.season_label(s['last'])}" if s["last"] != s["first"] else "")
                                 + f": {s['games']} games, {s['goals']} goals" for s in spells]
    if p["team_uid"]:
        joined = q._one(connection, "select t.date, t.kind, t.fee, f.name from_name from transfers t join teams n on n.fm_uid = t.to_uid "
                                    "left join teams f on f.fm_uid = t.from_uid join teams now on now.fm_uid = :team "
                                    "where t.person_uid = :u and coalesce(n.club_uid, n.fm_uid) = coalesce(now.club_uid, now.fm_uid) "
                                    "order by t.date desc limit 1", u=uid, team=p["team_uid"])
        if joined and joined["kind"] == YOUTH_INTAKE:
            row["joined_club_now"] = f"came through the club's youth intake on {joined['date']}"
        elif joined:
            row["joined_club_now"] = (f"{q.TRANSFER_KINDS.get(joined['kind'], 'joined').lower()} from {joined['from_name'] or 'no club'} on "
                                      f"{joined['date']}" + (f", £{joined['fee'] / 1e6:.1f}m" if joined["fee"] else ""))
    if p["reputation_world"] is not None:
        row["world_standing"] = _standing(p["reputation_world"])
    if p["staff_job"] == "Manager":
        profile = q.manager_profile(connection, uid)
        if profile:  # the game's own profile: what kind of coach he is
            row["manager_profile"] = {k: v for k, v in {"coaching_style": profile["coaching_style"], "tactical_style": profile["tactical_style"],
                                                        "preferred_formation": profile["preferred_formation"]}.items() if v}
    talk = moods.public_talk(connection, uid, published)
    if talk:  # only what has reached his team-mates; a concern he keeps to himself is not told
        row["dressing_room_talk"] = talk
    if p["staff_job"]:
        team = q.teams(connection, [p["staff_team_uid"]]).get(p["staff_team_uid"], {}).get("name")
        row["works_now_as"] = f"{p['staff_job']} at {team}" + (f", since {p['staff_since']}" if p["staff_since"] else "")
    return row


def _club(connection: Connection, uid: int, published: date) -> dict | None:
    c = q._one(connection, "select c.name, n.name country, t.reputation from clubs c left join nations n on n.fm_uid = c.league_nation_uid "
                           "left join teams t on t.fm_uid = c.fm_uid where c.fm_uid = :u", u=uid)
    if c is None:
        return None
    row: dict = {"name": c["name"]}
    if c["country"]:
        row["country"] = c["country"]
    season = published.year if published.month >= 7 else published.year - 1
    league = q._one(connection, "select c.name, c.level, count(*) n from player_seasons ps join competitions c on c.fm_uid = ps.league_uid "
                                "where ps.team_uid = :u and ps.kind = 1 and ps.season_start in (:s, :p) group by ps.league_uid "
                                "order by ps.season_start desc, n desc limit 1", u=uid, s=season, p=season - 1)
    if league:
        row["league"] = league["name"]
        if league["level"]:
            row["league_level"] = league["level"]
    titles = q._one(connection, "select count(*) n from edition_places p join competitions c on c.fm_uid = p.competition_uid "
                                "where p.team_uid = :u and p.place = 1 and c.scope in ('league', 'cup', 'continental')", u=uid)
    if titles and titles["n"]:
        row["major_titles"] = titles["n"]
    if c["reputation"] is not None:
        row["stature"] = _standing(c["reputation"])
    runs = _title_runs(connection, uid, published)
    if runs:
        row["title_runs"] = runs
    background = story_notebooks.club_background(uid, limit=CLUB_BACKGROUND_LIMIT)
    if background:
        row["background"] = background
    return row


TITLE_RUN = 3  # wins in a row that make a run worth naming
_PLACE_WORDS = {2: "the runners-up", 3: "third", 4: "fourth"}


def _title_runs(connection: Connection, uid: int, published: date) -> list[str]:
    """Each trophy the club has won in runs: how often it won it, the last time, and every run of TITLE_RUN or more, said
    whole: still going or ended, and how. The background text names runs loosely, and a writer once ended the wrong one
    ("the run of 41 league titles ended in 2062/63", the season it won the 41st). Only the longest run was given before,
    so a writer read 'Carabao Cup: 10 in a row, ended after 2050/51' as 'not lifted since', though Chelsea won six in a
    row from 2052/53 and again in 2060/61 (26 Oct 2026)."""
    found = []
    for honour in q.club_honours(connection, uid):
        labels = {int(x[:4]): x for x in honour["seasons"] if x[:4].isdigit()}
        years = sorted(labels)
        if not years:
            continue
        runs, start = [], years[0]
        for previous, year in zip(years, years[1:]):
            if year != previous + 1:
                runs.append((start, previous))
                start = year
        runs.append((start, years[-1]))
        long = [(a, b) for a, b in runs if b - a + 1 >= TITLE_RUN]
        if not long:
            continue
        latest = q._one(connection, "select e.year, e.season_label from competition_editions e where e.competition_uid = :c "
                                    "and e.decided_on <= :d order by e.decided_on desc limit 1", c=honour["competition_uid"], d=str(published))
        name = q._one(connection, "select name from competitions where fm_uid = :c", c=honour["competition_uid"])
        if latest is None or name is None:
            continue
        parts = []
        for a, b in long:
            span = f"{b - a + 1} in a row, {labels[a]} to {labels[b]}"
            if labels[b] == latest["season_label"]:
                parts.append(f"{span}, still going (won the latest, {latest['season_label']})")
            elif int(latest["season_label"][:4]) == b + 1:
                place = q._one(connection, "select place from edition_places where competition_uid = :c and year = :y and team_uid = :u",
                               c=honour["competition_uid"], y=latest["year"], u=uid)
                how = f"finished {_PLACE_WORDS.get(place['place'], 'lower')}" if place else "did not win it"
                parts.append(f"{span}, ended in {latest['season_label']} (the club {how})")
            else:
                parts.append(f"{span}, ended after {labels[b]}")
        found.append(f"{name['name']}: won {len(years)} times, the last in {labels[years[-1]]}; runs: " + "; ".join(parts))
    return found


_STANDING = (  # the game's reputation bands, each in several wordings so no phrase becomes the papers' habit
    (9000, ("a global giant", "among the very biggest clubs in the game", "a household name on every continent",
            "a superclub", "one of the sport's great powers")),
    (7500, ("internationally famous", "a big name abroad", "known far beyond its borders", "a club with a worldwide following",
            "a regular name in the international game")),
    (6000, ("well known across its continent", "a respected name in continental football", "a familiar name beyond its league")),
    (4000, ("well known at home", "a national name", "a club its country knows well")),
    (2000, ("known locally", "a modest club with a local following", "a small club")),
)


def _standing(reputation: int) -> str:
    """The game's reputation (0-10 000) in words, so the writer does not quote the number; a different wording each time."""
    for floor, words in _STANDING:
        if reputation >= floor:
            return random.choice(words)
    return random.choice(("little known", "an obscure club", "hardly known outside its town"))


# Phrases the papers lean on too much: a fixed list, and the phrases most used in recent articles.
CLICHES = ("far ahead of his age", "ahead of his age group", "for his age group", "against his age group", "famous across the world",
           "one of the biggest names in world football", "the question now is", "goal difference of plus")
OVERUSED_ARTICLES = 200  # the recent articles searched for overused phrases
OVERUSED_IN = 5  # a four-word phrase in this many of them is overused
OVERUSED_SHOWN = 20
_STOP = {"the", "of", "a", "and", "in", "to", "his", "at", "for", "with", "on", "that", "was", "is", "as", "he", "it", "by",
         "from", "an", "has", "have", "be", "their", "they", "who", "had", "but", "this", "not", "were", "which"}
_overused_cache: dict = {}


def overused_phrases() -> list[str]:
    """The fixed clichés and the four-word phrases (without names) found in many recent articles."""
    cached = _overused_cache.get("phrases")
    if cached and time.monotonic() - cached[0] < 300:
        return cached[1]
    from app.models.world_soccer_article import WorldSoccerArticle

    bodies = [body for (body,) in db.session.query(WorldSoccerArticle.body).order_by(WorldSoccerArticle.id.desc())
              .limit(OVERUSED_ARTICLES)]
    counts: Counter = Counter()
    for body in bodies:
        words = re.findall(r"[A-Za-z][A-Za-z'’-]*", body or "")
        grams = set()
        for i in range(len(words) - 3):
            gram = words[i:i + 4]
            if (any(word[0].isupper() for word in gram) or sum(word.lower() in _STOP for word in gram) > 2
                    or any(word in ("th", "st", "nd", "rd") for word in gram)):  # "77th" leaves a bare "th"
                continue  # names and competitions may repeat; mostly small words are not a phrase
            grams.add(" ".join(word.lower() for word in gram))
        counts.update(grams)
    found = [gram for gram, n in counts.most_common(OVERUSED_SHOWN * 3) if n >= OVERUSED_IN]
    phrases = list(dict.fromkeys(list(CLICHES) + found))[:len(CLICHES) + OVERUSED_SHOWN]
    _overused_cache["phrases"] = (time.monotonic(), phrases)
    return phrases


def pick_writer(outlet: Outlet, rng: random.Random | None = None) -> dict:
    """One of the outlet's writers, at random; the outlet's byline when it has no staff."""
    staff = [writer.as_dict() for writer in outlet.staff]
    return (rng or random).choice(staff) if staff else {"id": None, "name": outlet.byline, "persona": "", "style": "", "quirks": ""}


def _persona(writer: dict) -> str:
    parts = [f"Who you are: {writer['persona']}" if writer.get("persona") else "",
             f"How you write: {writer['style']}" if writer.get("style") else "",
             f"Your habits: {writer['quirks']}" if writer.get("quirks") else ""]
    return " ".join(part.rstrip(".") + "." for part in parts if part) + (" " if any(parts) else "")


def pick_form(rng: random.Random | None = None) -> dict:
    """An opening and a headline form, chosen at random (an edition deals them out with deal_forms)."""
    rng = rng or random.Random()
    return {"opening": rng.choice(OPENINGS), "headline": rng.choice(HEADLINES)}


def deal_forms(count: int, rng: random.Random | None = None) -> list[dict]:
    """Different openings and headline forms for the articles of one edition."""
    rng = rng or random.Random()
    openings, headlines = list(OPENINGS), list(HEADLINES)
    rng.shuffle(openings)
    rng.shuffle(headlines)
    return [{"opening": openings[i % len(openings)], "headline": headlines[i % len(headlines)]} for i in range(count)]


def write_article(outlet: Outlet, lead: Lead, published: date, prominence: str = "standard", writer: dict | None = None,
                  form: dict | None = None, earlier: list[dict] | None = None, related: list[dict] | None = None) -> dict:
    """A checked article by one of the outlet's writers (a random one when none is given), in one form:
    headline, standfirst, body, the byline, the model and the notebook it was written from."""
    if prominence not in PROMINENCE:
        raise StoryError(f"Unknown article length: {prominence}.")
    with db.engines[world_models.BIND].connect() as connection:
        notebook = article_notebook(connection, lead, published)
    if earlier:
        notebook["earlier_coverage"] = earlier
    days = (published - lead.date).days
    if days > 0:  # 27 Oct 2026: a 29 December game was written as if played on the 1 January edition's day
        notebook["when_it_happened"] = (f"{lead.date:%A %-d %B %Y}, {days} day{'s' if days > 1 else ''} before this edition"
                                        + ("; a round can span several days, each game on its own date" if lead.kind == "league_round" else ""))
    if related:
        notebook["related_stories"] = related
    model = get_papers_model()
    key = resolve_ai_api_key(None, model=model)
    if not key:
        raise StoryError("No API key is set for the papers' AI model chosen in Settings.")
    writer = writer or pick_writer(outlet)
    form = form or pick_form()
    if lead.kind in COLUMN_OPENINGS:  # a column opens its own way, not as a news story (27 Oct 2026: "Four." opened a trip)
        form = {**form, "opening": COLUMN_OPENINGS[lead.kind]}
    prompt = PROMPT.format(writer=writer["name"], name=outlet.name, tagline=f" ({outlet.tagline})" if outlet.tagline else "",
                           persona=_persona(writer), voice=outlet.voice, length=PROMINENCE[prominence],
                           published=published.strftime("%-d %B %Y"), opening=form["opening"], headline_form=form["headline"])
    if lead.kind in KIND_NOTES:
        prompt += " " + KIND_NOTES[lead.kind]
    if lead.facts.get("in_crisis"):
        prompt += (" in_crisis (in the story facts) names a team in crisis by the papers' reading: let it colour the piece "
                   "(a crisis-hit side, a manager under strain) in a phrase or two; it is not the story.")
    if notebook.get("connections"):
        prompt += (" connections lists players now at one side who once played for the other: weave these stories in "
                   "where they add to the story (an old boy deciding a final against his former club is magic).")
    if related:
        prompt += (" related_stories are other news of these weeks about the same players or clubs: cross-reference one "
                   "or two where they enrich this story, the way a novelist lets stories cross, without retelling them.")
    avoid = overused_phrases()
    if avoid:
        prompt += (" These phrases have been used far too often in the papers lately; do not use them or close variants, "
                   "find your own words: " + "; ".join(f'"{phrase}"' for phrase in avoid) + ".")
    if earlier:
        prompt += (" earlier_coverage is what the papers have already written about these people and this subject, newest "
                   "first ('about' says whom each piece concerned): continue the story, refer back where it helps ('three "
                   "months after his move…', 'the old boy who decided the final'), and never repeat it. A piece whose "
                   "what_it_was is a preview looked ahead to a game: its headline tells nothing that happened, and a game "
                   "not yet played has no result, no late show and no hero.")
    prompt += archive_questions.PROMPT_NOTE
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(notebook, ensure_ascii=False, default=str)}]
    article = _ask_with_questions(model, key, messages, _as_of(lead, published), notebook)
    faults = text_faults(_all_text(article), notebook)
    if faults:
        messages = [{"role": "system", "content": prompt},  # the answers are in the notebook now: no tool turns to carry
                    {"role": "user", "content": json.dumps(notebook, ensure_ascii=False, default=str)},
                    {"role": "assistant", "content": json.dumps(article, ensure_ascii=False)},
                     {"role": "user", "content": "Write the article again, fixing these faults (details not in the notebook must go): "
                                                 + "; ".join(faults)}]
        article = _ask(model, key, messages)
        faults = hard_faults(text_faults(_all_text(article), notebook))
        if faults:
            raise StoryError("The AI model's second draft still had faults: " + "; ".join(faults[:5]))
    article = _fact_checked(model, key, prompt, notebook, article, lead, published)
    if article.get("picture") and not picture_is_clean(article["picture"], notebook):
        article.pop("picture")  # the fixed scenes of its kind serve instead
    return {**article, "byline": writer["name"], "writer_id": writer.get("id"), "model": model, "notebook": notebook}


_PERSON_KEYS = ("name", "person", "player", "manager", "scorer")
_PLACE_KEYS = ("club", "team", "home", "away", "ground", "stadium")
_COMMON = {"real", "sporting", "athletic", "atletico", "inter", "united", "city", "town", "county", "national", "royal", "old",
           "new", "young", "saint", "park", "green", "rovers", "wanderers", "stadium", "ground", "the", "club", "football", "fc"}


def _names(value, found: set[str]) -> set[str]:
    """The words a picture must not hold: every full name the notebook gives, each person's surname, each club's or
    ground's first word, leaving out the common ones (a "town" is a fine thing to photograph)."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and (key in _PERSON_KEYS + _PLACE_KEYS or key.endswith("_name")):
                words = [w.lower() for w in re.findall(r"[^\W\d_]+", item)]
                if not words or len(item) > 80:
                    continue
                found.add(" ".join(words))
                last, first = words[-1], words[0]
                if key in _PERSON_KEYS or key.endswith("_name"):
                    found.add(last)
                else:
                    found.add(first)
            else:
                _names(item, found)
    elif isinstance(value, list):
        for item in value:
            _names(item, found)
    return {name for name in found if name not in _COMMON and len(name) > 2}


def picture_is_clean(picture: str, notebook: dict) -> bool:
    """A picture a model can draw: no digits and no name from the notebook (a name is drawn as a stranger, or as text)."""
    if re.search(r"\d", picture):
        return False
    text = " " + " ".join(re.findall(r"[^\W\d_]+", picture.lower())) + " "
    return not any(f" {name} " in text for name in _names(notebook, set()))


MATCH_REPORTS = {"club_report", "promised_report", "tournament_report"}


def _as_of(lead, published: date) -> date:
    """The day the writer's questions are answered for: a match report's game day, so the archive tells nothing that
    happened after the game (the report of Chelsea 3-1 Shamrock Rovers led with the Forest game four days later, 3 Feb
    2064); the edition's date for anything else."""
    ref = lead.refs[0] if lead.kind in MATCH_REPORTS and lead.refs else ""
    if ref.startswith("match:"):
        try:
            return min(published, date.fromisoformat(ref.split(":")[1]))
        except ValueError:
            pass
    return published


def _ask_with_questions(model: str, key: str, messages: list[dict], published: date, notebook: dict) -> dict:
    """The article, after the writer's questions to the archive; the answers join the notebook, so the fact check counts
    them as facts."""
    with db.engines[world_models.BIND].connect() as connection:
        archive = archive_questions.Archive(connection, published)
        try:
            answer, asked = request_with_questions(model, key, messages, archive_questions.TOOLS, archive.answer,
                                                   archive_questions.MAX_QUESTIONS, temperature=TEMPERATURE)
        except StoryError as exc:
            if "answer" not in str(exc):
                raise
            return _ask(model, key, messages)
    if asked:
        notebook["answers_from_the_archive"] = asked
    try:
        return _article(answer)
    except StoryError:
        return _ask(model, key, messages)


def _fact_checked(model: str, key: str, prompt: str, notebook: dict, article: dict, lead, published: date) -> dict:
    """The article after the fact checker: its hard facts checked in the archive as of the article's day; the wrong and
    the unsupported go back to the writer once, and what is still wrong after that is cut (2 Nov 2026)."""
    as_of = _as_of(lead, published)
    # the claims are listed by the checker's own hosted model, whatever writes (3 Nov 2026: Gemma 4 on LM Studio listed too
    # few and let three wrong weekdays through); without its key, the writer's model lists them
    check_model = get_checker_model()
    check_key = resolve_ai_api_key(None, model=check_model)
    check_model, check_key = (check_model, check_key) if check_key else (model, key)
    with db.engines[world_models.BIND].connect() as connection:
        game = _game(connection, lead)
        checker = fact_check.Checker(connection, as_of, game)
        found = fact_check.problems([checker.check(c) for c in fact_check.claims(check_model, check_key, _all_text(article), notebook)])
        if not found:
            return article
        for f in found:
            current_app.logger.info("fact check: %s %r %s", f.verdict, f.sentence[:120], f.truth[:300])
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(notebook, ensure_ascii=False, default=str)},
                    {"role": "assistant", "content": json.dumps(article, ensure_ascii=False)},
                    {"role": "user", "content": fact_check.fix_note(found)}]
        fixed = _ask(model, key, messages)
        if hard_faults(text_faults(_all_text(fixed), {**notebook, "fact_check": [f.truth for f in found]})):
            fixed = article  # the fix brought new faults: the first draft, with the bad sentences cut
        still = fact_check.problems([checker.check(c) for c in fact_check.claims(check_model, check_key, _all_text(fixed), notebook)])
    if any(f.sentence and f.sentence in fixed["headline"] for f in still):
        raise StoryError("The fact checker found a wrong headline twice: " + still[0].sentence[:120])
    for f in still:
        current_app.logger.info("fact check, cut: %s %r %s", f.verdict, f.sentence[:120], f.truth[:300])
    return fact_check.cut(fixed, still) if still else fixed


def _game(connection, lead) -> dict | None:
    """The fixture a match report tells, from its first ref ("match:day:home:away")."""
    ref = lead.refs[0] if lead.refs else ""
    if not ref.startswith("match:"):
        return None
    try:
        _, day, home, away = ref.split(":")[:4]
        rows = connection.exec_driver_sql("select id from fixtures where date like ? and home_uid = ? and away_uid = ? limit 1",
                                          (f"{day}%", int(home), int(away))).fetchall()
    except ValueError:
        return None
    return {"id": rows[0][0]} if rows else None


def _ask(model: str, key: str, messages: list[dict]) -> dict:
    """The model's article; an answer that is not an article (broken JSON, no body) is asked for once more."""
    try:
        return _article(request_json(model, key, messages, temperature=TEMPERATURE))
    except StoryError as exc:
        if "answer" not in str(exc):
            raise
        return _article(request_json(model, key, messages, temperature=TEMPERATURE))


VERDICTS = ("right", "early", "wrong")


def _article(answer: dict) -> dict:
    article = {key: str(answer.get(key) or "").strip() for key in ("headline", "standfirst", "body")}
    if not article["headline"] or not article["body"]:
        raise StoryError("The AI model's answer had no headline or no body.")
    picture = " ".join(str(answer.get("picture") or "").split())
    if 20 <= len(picture) <= PICTURE_MAX:
        article["picture"] = picture
    verdict = str(answer.get("verdict") or "").strip().lower()
    if verdict in VERDICTS:  # a scouting follow-up's call on the paper's own report
        article["verdict"] = verdict
    return article


def _all_text(article: dict) -> str:
    return "\n".join((article["headline"], article["standfirst"], article["body"]))
