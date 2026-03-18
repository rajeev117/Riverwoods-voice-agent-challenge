graph TD;
  A[CRM / Scheduler] --> B[/internal/send/];
  B --> C[Twilio Call];

  C --> D{Answered?};

  D -->|No| E[Voicemail];
  E --> F[Hangup];

  D -->|Yes| G[Greeting];
  G --> H[Gather Input];

  H --> I[STT];
  I --> J{Cached?};

  J -->|Yes| K[Cached Response];
  J -->|No| L[OpenAI];

  K --> M[TTS];
  L --> M;

  M --> N{End Call?};

  N -->|No| H;
  N -->|Yes| O[Goodbye];

  O --> P[Hangup];
  P --> Q[Save to DB];
