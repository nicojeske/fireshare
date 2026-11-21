import React from 'react'
import {
  Autocomplete,
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  IconButton,
  InputAdornment,
  NativeSelect,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import SearchIcon from '@mui/icons-material/Search'
import { TagService } from '../../services'

const DISCORD_ID = /^\d{17,20}$/
const TAG_NAME_MAX = 12 // mirrors the server's limit in api/tag.py
const SEARCH_THRESHOLD = 8 // show a filter once the list gets long
const DEV_MODE_URL =
  'https://support.discord.com/hc/en-us/articles/206346498-Where-can-I-find-my-User-Server-Message-ID'

const UPLOAD_LIMITS = [
  { value: 20, label: '20 MB (no boost)' },
  { value: 50, label: '50 MB (boost level 2)' },
  { value: 100, label: '100 MB (boost level 3)' },
]

// The server stores tag names with spaces turned into underscores
const normalizeTagName = (name) => name.trim().replace(/ /g, '_')

const TagDot = ({ color }) => (
  <Box sx={{ width: 10, height: 10, borderRadius: '50%', flexShrink: 0, bgcolor: color || 'grey.600' }} />
)

const TypeSelect = ({ value, onChange }) => (
  <NativeSelect value={value} onChange={(e) => onChange(e.target.value)} sx={{ width: 70, flexShrink: 0, fontSize: 14 }}>
    <option value="user">User</option>
    <option value="role">Role</option>
  </NativeSelect>
)

// Maps each tag to the Discord user or role it should ping. Webhooks can only
// mention by numeric id, never by username, so that is what gets stored.
// integrations: the config's integrations block; onChange(patch) merges keys into it.
const DiscordTagMentions = ({ integrations = {}, onChange }) => {
  const [tags, setTags] = React.useState([])
  const [filter, setFilter] = React.useState('')
  const [draft, setDraft] = React.useState({ tag: null, type: 'user', id: '' })
  const [adding, setAdding] = React.useState(false)
  const [addError, setAddError] = React.useState('')
  const mentions = integrations.discord_tag_mentions || {}
  const pingOnTagAdd = integrations.discord_ping_on_tag_add ?? true
  const attachPreview = integrations.discord_attach_preview ?? true
  // Anything below 20 MB (Discord's free limit) is treated as 20 by the server too
  const uploadLimit = Math.max(20, integrations.discord_upload_limit_mb ?? 20)

  const loadTags = React.useCallback(
    () =>
      TagService.getTags()
        .then((res) => setTags(res.data || []))
        .catch(() => setTags([])),
    [],
  )

  React.useEffect(() => {
    loadTags()
  }, [loadTags])

  const setMention = (tagId, entry) => {
    const next = { ...mentions }
    if (entry) next[String(tagId)] = entry
    else delete next[String(tagId)]
    onChange({ discord_tag_mentions: next })
  }

  const update = (tagId, patch) => {
    const entry = { type: 'user', id: '', ...mentions[String(tagId)], ...patch }
    setMention(tagId, { type: entry.type, id: entry.id.trim() })
  }

  // Mappings for tags that were deleted are skipped; the server ignores them too
  const mapped = tags.filter((t) => mentions[String(t.id)])
  const unmapped = tags.filter((t) => !mentions[String(t.id)])
  const query = filter.trim().toLowerCase()
  const visible = query
    ? mapped.filter((t) => t.name.toLowerCase().includes(query) || mentions[String(t.id)].id.includes(query))
    : mapped

  const draftName = typeof draft.tag === 'string' ? normalizeTagName(draft.tag) : draft.tag?.name || ''
  const draftIdInvalid = draft.id.trim() !== '' && !DISCORD_ID.test(draft.id.trim())
  const draftNameTooLong = draftName.length > TAG_NAME_MAX
  const canAdd = draftName !== '' && !draftNameTooLong && DISCORD_ID.test(draft.id.trim()) && !adding

  const handleAdd = async () => {
    if (!canAdd) return
    setAddError('')
    let tag = typeof draft.tag === 'object' ? draft.tag : tags.find((t) => t.name.toLowerCase() === draftName.toLowerCase())
    if (!tag) {
      setAdding(true)
      try {
        const res = await TagService.createTag({ name: draftName })
        tag = res.data
      } catch (err) {
        // 409 means the tag already exists (e.g. created elsewhere since we loaded); use it
        if (err.response?.status === 409 && err.response.data?.id) tag = err.response.data
        else {
          setAddError(typeof err.response?.data === 'string' ? err.response.data : 'Could not create tag')
          setAdding(false)
          return
        }
      }
      await loadTags()
      setAdding(false)
    }
    if (mentions[String(tag.id)]) {
      setAddError(`"${tag.name}" is already mapped — edit it above`)
      return
    }
    setMention(tag.id, { type: draft.type, id: draft.id.trim() })
    setDraft({ tag: null, type: draft.type, id: '' })
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

      {mapped.length > SEARCH_THRESHOLD && (
        <TextField
          size="small"
          placeholder="Filter by tag or ID"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon fontSize="small" />
              </InputAdornment>
            ),
          }}
        />
      )}

      {mapped.length === 0 && (
        <Typography variant="caption" color="text.secondary">
          No mentions yet. Add one below.
        </Typography>
      )}

      {visible.map((tag) => {
        const entry = mentions[String(tag.id)]
        const invalid = entry.id !== '' && !DISCORD_ID.test(entry.id)
        return (
          <Box key={tag.id} sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <TagDot color={tag.color} />
            <Typography variant="body2" sx={{ width: 110, flexShrink: 0 }} noWrap title={tag.name}>
              {tag.name}
            </Typography>
            <TypeSelect value={entry.type} onChange={(type) => update(tag.id, { type })} />
            <TextField
              size="small"
              placeholder="123456789012345678"
              value={entry.id}
              error={invalid}
              helperText={invalid ? 'IDs are 17–20 digits' : null}
              onChange={(e) => update(tag.id, { id: e.target.value })}
              sx={{ flexGrow: 1 }}
            />
            <IconButton size="small" aria-label={`Remove mention for ${tag.name}`} onClick={() => setMention(tag.id, null)}>
              <DeleteOutlineIcon fontSize="small" />
            </IconButton>
          </Box>
        )
      })}
      {query && visible.length === 0 && (
        <Typography variant="caption" color="text.secondary">
          No mentions match "{filter.trim()}".
        </Typography>
      )}

      {/* Add a mention: pick an unmapped tag, or type a new name to create the tag */}
      <Box
        component="form"
        onSubmit={(e) => {
          e.preventDefault()
          handleAdd()
        }}
        sx={{ border: 1, borderColor: 'divider', borderRadius: 1, p: 1.5, mt: 0.5 }}
      >
        <Typography variant="body2" sx={{ fontWeight: 600, mb: 1 }}>
          Add mention
        </Typography>
        <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1, flexWrap: 'wrap' }}>
          <Autocomplete
            freeSolo
            size="small"
            options={unmapped}
            value={draft.tag}
            getOptionLabel={(o) => (typeof o === 'string' ? o : o.name)}
            renderOption={(props, o) => (
              <Box component="li" {...props} key={o.id} sx={{ display: 'flex', gap: 1 }}>
                <TagDot color={o.color} />
                {o.name}
              </Box>
            )}
            onChange={(_, value) => setDraft((d) => ({ ...d, tag: value }))}
            onInputChange={(_, value, reason) => {
              if (reason === 'input') setDraft((d) => ({ ...d, tag: value }))
            }}
            sx={{ width: 160, flexShrink: 0 }}
            renderInput={(params) => (
              <TextField
                {...params}
                placeholder="Tag"
                error={draftNameTooLong}
                helperText={
                  draftNameTooLong
                    ? `Max ${TAG_NAME_MAX} characters`
                    : draftName && !tags.some((t) => t.name.toLowerCase() === draftName.toLowerCase())
                      ? 'New tag will be created'
                      : null
                }
              />
            )}
          />
          <TypeSelect value={draft.type} onChange={(type) => setDraft((d) => ({ ...d, type }))} />
          <TextField
            size="small"
            placeholder="Discord ID"
            value={draft.id}
            error={draftIdInvalid}
            helperText={draftIdInvalid ? 'IDs are 17–20 digits' : null}
            onChange={(e) => setDraft((d) => ({ ...d, id: e.target.value }))}
            sx={{ flex: '1 1 160px' }}
          />
          <Button type="submit" variant="outlined" startIcon={<AddIcon />} disabled={!canAdd} sx={{ height: 40 }}>
            Add
          </Button>
        </Box>
        {addError && (
          <Typography variant="caption" color="error">
            {addError}
          </Typography>
        )}
      </Box>

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
