# The social feed

Fans, clubs, players and journalists post about what happened in the save, on a Feed page in game time.
Settings can turn the whole feed off: nothing new is posted, the Feed leaves the menu, the posts are kept.

## Data

In the main database (the world database is rebuilt from the saves), keyed by FM ids:

- `social_accounts`: handle, name, kind (club, fan, player, journalist), club, person, a fan's persona.
- `social_posts`: account, game date, kind, the event's key (one post per account and event, so an import of the
  same days again posts nothing twice), text, linked names, the post it answers, likes and reposts.

## Steps

1. **Done.** The clubs' accounts (the followed clubs and the 40 best-known; a transfer of £25m or more brings in
   both its clubs) post from templates after each import: results with the scorers, signings and sales, titles,
   managers leaving and arriving. Likes and reposts come from the club's fame, the event's size and chance.
   `app/services/social_feed.py`, the Feed at `/feed/`.
2. **Done.** Fans (`app/services/social_fans.py`): six for each club that gets a thread, made once and kept, each
   with a first name from the club's country, a handle and a temper (optimist, doom-monger, numbers nerd, old-timer,
   wind-up merchant, ultra, always online, critic). "Write the fans' reactions" on the Feed writes threads under the
   8 best club posts without one (likes times the kind's weight; the followed clubs count five times), in the
   background. One AI call a thread: the post, both clubs' recent results and last meetings, the rivalry, the people
   in the post (age, position, recent seasons, fee, managerial spells), and a cast of fans of both clubs and of one of
   the posting club's fiercest rivals. Replies with years, scores or large numbers not in the notebook are asked for
   again once, then dropped. A reserve or youth team's fans support its club; games of a competition the save does
   not list (a pre-season tournament) are friendlies. Each result in a notebook says its competition, how long
   before the post it was and whether it was last season, so a defeat of May does not read like last week's.

   **The slow writer.** While the app runs, the feed is on and Ollama answers, the Mac app's server writes the fans'
   threads with a local model (the one chosen in Settings if it is local, else a Gemma, else the smallest installed),
   never while an import or another job runs, up to 24 threads after each import. One AI call writes a whole thread
   (the cheap way: the notebook is read once), but its replies are shown one by one, 10 to 40 seconds apart
   (`shown_at`), after those already waiting; the writer keeps about two threads ahead and writes again when the queue
   runs short. It answers the posts of the last 10 game days, a post's claim halving with every 4 days of age. The
   Feed's "Talking now" tab orders the threads by their newest reply, and "New replies from the fans" opens it.
   The "Write the fans' reactions" button is gone.

3. **Done.** Players: their own accounts (with their faces) post from templates after a signing of £10m or more
   and after two or more goals in a game; in the fans' threads a player with an account answers once, media-trained.
   Journalists: after each news cycle the writer of each edition's lead story posts it, with a link; The Transfer
   Wire posts every move of £15m or more. Hashtags for clubs and competitions (#Chelsea, #PremierDivision) on the
   posts, offered to the fans; a "Trending" card (hashtags and people of the last 7 game days) and a page for each
   hashtag. A Social tab on the club page (the club's posts, its fans' latest replies) and on the person page (his
   own posts and those that name him). The fans' notebook also holds each club's honours of the last twelve months.
4. **Done.** Predictions and heat (`app/services/social_predictions.py`). After each import the clubs post their big
   games of the next 7 days (the followed clubs' games, derbies, games between two of the best-known clubs), and three
   fans of each side answer with a score from templates, bent by their temper (the optimist sees a rout, the
   doom-monger a defeat, the always-online fan something absurd); the score is kept. When the result is in, a fan
   who called it exactly says so under the result, and the AI thread under the result gets every prediction and
   its verdict, with the predictors in the cast. Every post has a heat (0-100): a derby, an upset, a sale to a
   rival, a sacking, a big fee, a shoot-out; hot posts get more likes, come first for the fans, draw longer threads
   (8-12 replies) with more rival fans, and show 🔥. A transfer also burns with the player's standing in the save: a
   Legend or Icon leaving (+40, its own farewell), a Favoured player (+20), a man the buying club dislikes (+35), a
   legend coming home (+30, "Welcome home"); the fans' facts say who is whose Legend or enemy.
5. **Done.** The match itself. The reader decodes each shot of a full match report (minute and second, goal, off
   target, woodwork, saved or blocked, where it crossed the line; `fm_save.Shot`, table `match_shots`), and
   `world_queries.match_summary` gives the fans the player of the match, xG, shots, possession, woodwork with the
   minute, red cards, injuries, the best and worst ratings, a late winner and a win against the run of play. Red
   cards, a late winner, a robbery and injuries also raise a result's heat. Disallowed goals are not in the save's
   reports (not found yet).
