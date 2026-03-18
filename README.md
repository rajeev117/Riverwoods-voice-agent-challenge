flowchart TD

    A[CRM / Scheduler]
    A --> B[/internal/send/]
    B --> C[Twilio Outbound Call]

    C --> D{Call Answered?}

    D -->|Voicemail| E[Leave Voicemail]
    E --> F[Hangup]

    D -->|Human Answers| G[/voice (Greeting)/]

    G --> H[/gather/]

    H --> I[Twilio STT]

    I --> J{Cached in Pinecone?}

    J -->|Yes| K[Return Cached Response]
    J -->|No| L[OpenAI Generate Reply]

    K --> M[Convert to Speech]
    L --> M

    M --> N{END_CALL?}

    N -->|No| H
    N -->|Yes| O[Say Goodbye]

    O --> P[Hangup]
    P --> Q[Save Conversation to MongoDB]
