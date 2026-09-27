## Notifications

Fireshare supports notifications when a new video is uploaded, with two integration options: **Discord** and a **Generic Webhook**.

---

### Discord

Since gaming and Discord go hand-in-hand, Fireshare includes a dedicated Discord integration. When a new video is uploaded, it will automatically send a notification to the Discord channel of your choice.

**Setup:** Add the webhook URL for the Discord channel you want notifications sent to.

> Don't have a webhook URL yet? Learn how to create one here: [Discord — Intro to Webhooks](https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks)

**Docker ENV example:**
```
DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/123456789/abcdefghijklmnopqrstuvwxyz'
```

#### What gets posted

Only **public** videos are posted, and each video is posted once. A video that starts out private is posted the first time it is made public.

When transcoding is enabled, the post is sent once the video's transcode has finished.

The post contains a card with the title, game, length, quality, tags and uploader, and a **Watch on Fireshare** button. The title and button open the watch page, which plays the original file. The card uses the color of the video's first colored tag.

#### Video preview

Discord often can't play original recordings inline (HEVC, very large files, or the index at the end of the file). So every post gets a small **preview clip**: 720p H.264, up to 60 fps, sized to fit the server's upload limit. It's stored next to the video's other files as `<id>-discord.mp4`.

- If the clip fits the upload limit, it's **attached** to the post and always plays inline.
- Otherwise (long clips, or attaching turned off), the post **links directly** to the preview file, and Discord shows its player for that link.
- Password-protected videos get no preview; the card shows the poster instead.

Set the upload limit on the Integrations page to match your server's boost level: 10 MB with no boost, 50 MB at level 2, 100 MB at level 3.

#### Fixing existing transcodes

Transcodes are now written with the index at the start of the file (`+faststart`), so playback can begin right away. To fix transcodes created before this change without re-encoding them, run:

```
docker exec -it fireshare fireshare faststart-transcodes --dry-run   # list affected files
docker exec -it fireshare fireshare faststart-transcodes
```

#### Pinging people with tags

If your tags represent people, you can map each tag to a Discord user or role on the **Integrations** settings page. Everyone mapped to a tag on the video is mentioned in the post.

Discord webhooks can only mention by numeric ID, not by username. To copy an ID, turn on **Developer Mode** in Discord (User Settings → Advanced), then right-click a user or role and choose **Copy ID**.

When **Ping people when their tag is added to an already posted video** is on, adding a mapped tag later sends a short follow-up message that pings only the newly tagged person.

Titles and descriptions can never trigger `@everyone`, `@here` or unmapped mentions.

**config.json example:**
```json
"integrations": {
  "discord_webhook_url": "https://discord.com/api/webhooks/...",
  "discord_tag_mentions": {
    "3": { "type": "user", "id": "123456789012345678" },
    "7": { "type": "role", "id": "223456789012345678" }
  },
  "discord_ping_on_tag_add": true
}
```
The keys of `discord_tag_mentions` are tag IDs.

---

### Generic Webhook

For any notification service that supports HTTP POST requests with a JSON payload, you can use the Generic Webhook integration. This allows Fireshare to send notifications to virtually any platform that supports webhooks.

**Setup:** You will need to provide two things:
1. The **POST URL** for your service's webhook endpoint
2. A **JSON payload** formatted for your specific service

Enter valid JSON into the "Generic Webhook JSON Payload" field on the Integrations page. Consult your service's webhook documentation to find the correct payload format.

**Example payload:**
```json
{
    "Title": "Fireshare",
    "message": "New Video Uploaded to Fireshare"
}
```

#### Including a Link to the Video

You can include a direct link to the newly uploaded video in your notification by using the `[video_url]` placeholder anywhere in your JSON payload.

**Example payload with video link:**
```json
{
    "Title": "Fireshare",
    "message": "New Video Uploaded to Fireshare [video_url]"
}
```

**What Fireshare will send to your service:**
```json
{
    "Title": "Fireshare",
    "message": "New Video Uploaded to Fireshare https://yourdomain.com/w/c415d34530d15b2892fa4a4e037b6c05"
}
```

#### A Note on Quote Syntax

JSON payloads use key/value pairs where strings are wrapped in quotes. Keep the following in mind:

- **GUI:** If you are pasting the payload through the Fireshare UI, just choose either single `'` or double `"` quotes for your strings — Fireshare will handle the rest.
- **Docker ENV:** You must use one type of quote to wrap the entire value, and the other type for the internal JSON strings.

**Docker ENV example:**
```
GENERIC_WEBHOOK_PAYLOAD='{"Title": "Fireshare", "message": "New Video Uploaded to Fireshare [video_url]"}'
# Note: this must be a single line
```

**Full Docker ENV example:**
```
GENERIC_WEBHOOK_URL='https://webhook.com/at/endpoint12345'
GENERIC_WEBHOOK_PAYLOAD='{"Title": "Fireshare", "message": "New Video Uploaded to Fireshare [video_url]"}'
# Both ENV variables must be set for the Generic Webhook to work
```
