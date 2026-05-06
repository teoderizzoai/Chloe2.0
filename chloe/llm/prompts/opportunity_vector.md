# World Opportunity Vector

Assess the current opportunity for Chloe to take actions across different channels.

## Context
- Time: {{time_of_day}} {{day_of_week}}
- Calendar today: {{calendar_events_today}}
- Last chat with Teo: {{last_chat_seen}}
- Spotify playing: {{spotify_playing}}

## Output
Return JSON matching the OpportunityVector schema:
{
  "messages": 0.0-1.0,    # How receptive is Teo to receiving a message right now?
  "spotify": 0.0-1.0,     # Is music context-appropriate?
  "calendar": 0.0-1.0,    # Would a calendar action be timely?
  "notes": 0.0-1.0,       # Is now a good time to add to notes?
  "web_search": 0.0-1.0,  # Can Chloe usefully search right now?
  "gmail": 0.0-1.0,       # Is email action appropriate?
  "reminders": 0.0-1.0    # Would a reminder be useful?
}

Low message opportunity: Teo is in back-to-back meetings, it's 02:00, or last chat was < 5 min ago.
High message opportunity: Morning/evening, last chat > 4 hours ago, no events blocking.
