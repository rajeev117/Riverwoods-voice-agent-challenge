flowchart TD
    A[CRM / Scheduler] --> B[/internal/send/]
    B --> C[Twilio Outbound Call]

    C --> D{Call Answered?}

    D -->|Voicemail| E[Leave Voicemail]
    E --> F[Hangup]

    D -->|Human Answers| G[/voice Greeting/]

    G --> H[/gather Input/]
    H --> I[Speech to Text]

    I --> J{Pinecone Cache?}

    J -->|Yes| K[Cached Response]
    J -->|No| L[OpenAI gpt-4o-mini]

    K --> M[Text to Speech]
    L --> M

    M --> N{End Call?}

    N -->|No| H
    N -->|Yes| O[Goodbye]

    O --> P[Hangup]
    P --> Q[Save to MongoDB]
