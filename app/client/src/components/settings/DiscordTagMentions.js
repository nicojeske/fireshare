import React from 'react'
import { Box, Checkbox, FormControlLabel, NativeSelect, Stack, TextField, Typography } from '@mui/material'
import { TagService } from '../../services'

const DISCORD_ID = /^\d{17,20}$/
const DEV_MODE_URL =
  'https://support.discord.com/hc/en-us/articles/206346498-Where-can-I-find-my-User-Server-Message-ID'

const UPLOAD_LIMITS = [
  { value: 20, label: '20 MB (no boost)' },
  { value: 50, label: '50 MB (boost level 2)' },
  { value: 100, label: '100 MB (boost level 3)' },
]

// Maps each tag to the Discord user or role it should ping. Webhooks can only
// mention by numeric id, never by username, so that is what gets stored.
// integrations: the config's integrations block; onChange(patch) merges keys into it.
const DiscordTagMentions = ({ integrations = {}, onChange }) => {
  const [tags, setTags] = React.useState([])
  const mentions = integrations.discord_tag_mentions || {}
  const pingOnTagAdd = integrations.discord_ping_on_tag_add ?? true
  const attachPreview = integrations.discord_attach_preview ?? true
  // Anything below 20 MB (Discord's free limit) is treated as 20 by the server too
  const uploadLimit = Math.max(20, integrations.discord_upload_limit_mb ?? 20)

  React.useEffect(() => {
    TagService.getTags()
      .then((res) => setTags(res.data || []))
      .catch(() => setTags([]))
  }, [])

  const update = (tagId, patch) => {
    const key = String(tagId)
    const entry = { type: 'user', id: '', ...mentions[key], ...patch }
    const next = { ...mentions }
    if (entry.id.trim() === '') delete next[key]
    else next[key] = { type: entry.type, id: entry.id.trim() }
    onChange({ discord_tag_mentions: next })
  }

  return (
    <Stack spacing={1}>
      <Typography variant="subtitle2">Tag → Discord mentions</Typography>
      <Typography variant="caption" color="text.secondary">
        People tagged in a new public video get pinged in the Discord post. Enter a Discord user or role ID (turn on
        Developer Mode, then right-click → Copy ID) -{' '}
        <a href={DEV_MODE_URL} target="_blank" rel="noopener noreferrer" style={{ color: '#2684FF', textDecoration: 'none' }}>
          How to find IDs
        </a>
      </Typography>
      {tags.length === 0 && (
        <Typography variant="caption" color="text.secondary">
          No tags yet. Create tags first to map them to Discord.
        </Typography>
      )}
      {tags.map((tag) => {
        const entry = mentions[String(tag.id)] || { type: 'user', id: '' }
        const invalid = entry.id !== '' && !DISCORD_ID.test(entry.id)
        return (
          <Box key={tag.id} sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Box
              sx={{ width: 10, height: 10, borderRadius: '50%', flexShrink: 0, bgcolor: tag.color || 'grey.600' }}
            />
            <Typography variant="body2" sx={{ width: 110, flexShrink: 0 }} noWrap title={tag.name}>
              {tag.name}
            </Typography>
            <NativeSelect
              value={entry.type}
              onChange={(e) => update(tag.id, { type: e.target.value })}
              sx={{ width: 70, flexShrink: 0, fontSize: 14 }}
            >
              <option value="user">User</option>
              <option value="role">Role</option>
            </NativeSelect>
            <TextField
              size="small"
              placeholder="123456789012345678"
              value={entry.id}
              error={invalid}
              helperText={invalid ? 'IDs are 17–20 digits' : null}
              onChange={(e) => update(tag.id, { id: e.target.value })}
              sx={{ flexGrow: 1 }}
            />
          </Box>
        )
      })}
      <FormControlLabel
        control={
          <Checkbox checked={pingOnTagAdd} onChange={(e) => onChange({ discord_ping_on_tag_add: e.target.checked })} />
        }
        label="Ping people when their tag is added to an already posted video"
      />
      <Typography variant="subtitle2" sx={{ pt: 1 }}>
        Video preview
      </Typography>
      <Typography variant="caption" color="text.secondary">
        Discord often can't play the original file (HEVC, large files), so every post gets a small H.264 preview clip.
        It's attached when it fits the server's upload limit, otherwise the post links to it.
      </Typography>
      <FormControlLabel
        control={
          <Checkbox checked={attachPreview} onChange={(e) => onChange({ discord_attach_preview: e.target.checked })} />
        }
        label="Attach the preview clip to the post"
      />
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Typography variant="body2">Server upload limit</Typography>
        <NativeSelect
          value={uploadLimit}
          disabled={!attachPreview}
          onChange={(e) => onChange({ discord_upload_limit_mb: Number(e.target.value) })}
          sx={{ fontSize: 14 }}
        >
          {UPLOAD_LIMITS.map((l) => (
            <option key={l.value} value={l.value}>
              {l.label}
            </option>
          ))}
        </NativeSelect>
      </Box>
    </Stack>
  )
}

export default DiscordTagMentions
