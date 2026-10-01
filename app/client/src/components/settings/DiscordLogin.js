import React from 'react'
import { Alert, Checkbox, FormControlLabel, NativeSelect, Stack, TextField, Typography } from '@mui/material'

const DISCORD_ID = /^\d{17,20}$/
const DEV_MODE_URL =
  'https://support.discord.com/hc/en-us/articles/206346498-Where-can-I-find-my-User-Server-Message-ID'

const PRESETS = [
  { value: 'viewer', label: 'Viewer (view only)' },
  { value: 'contributor', label: 'Contributor (upload, edit and delete own)' },
  { value: 'curator', label: 'Curator (manage everything but settings and users)' },
]

// "Sign in with Discord" for members of one server. The OAuth client id and secret
// are env vars (DISCORD_CLIENT_ID / DISCORD_CLIENT_SECRET); status says whether
// they are set and which redirect URI to register in the Developer Portal.
// integrations: the config's integrations block; onChange(patch) merges keys into it.
const DiscordLogin = ({ integrations = {}, status = {}, onChange }) => {
  const enabled = !!integrations.discord_login_enabled
  const guildId = integrations.discord_login_guild_id || ''
  const roleId = integrations.discord_login_required_role_id || ''
  const preset = integrations.discord_login_default_preset || 'contributor'
  const disableOnLeave = !!integrations.discord_login_disable_on_leave
  const guildInvalid = guildId !== '' && !DISCORD_ID.test(guildId)
  const roleInvalid = roleId !== '' && !DISCORD_ID.test(roleId)

  return (
    <Stack spacing={1.5}>
      <Typography variant="subtitle2">Sign in with Discord</Typography>
      <Typography variant="caption" color="text.secondary">
        Members of your Discord server can sign in without a password. An account is created on their first sign-in.
        Membership is checked at sign-in and again at least every 24 hours. Find IDs with Developer Mode, then
        right-click → Copy ID -{' '}
        <a href={DEV_MODE_URL} target="_blank" rel="noopener noreferrer" style={{ color: '#2684FF', textDecoration: 'none' }}>
          How to find IDs
        </a>
      </Typography>
      {!status.client_configured && (
        <Alert severity="info" variant="outlined">
          Set DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET (and DOMAIN or DISCORD_REDIRECT_URI) in the environment to use
          this.
        </Alert>
      )}
      {status.redirect_uri && (
        <Typography variant="caption" color="text.secondary">
          Redirect URI to add in the Discord Developer Portal (OAuth2 → Redirects):{' '}
          <code style={{ userSelect: 'all' }}>{status.redirect_uri}</code>
        </Typography>
      )}
      <FormControlLabel
        control={
          <Checkbox checked={enabled} onChange={(e) => onChange({ discord_login_enabled: e.target.checked })} />
        }
        label="Enable Sign in with Discord"
      />
      <TextField
        size="small"
        label="Discord Server ID"
        value={guildId}
        error={guildInvalid || (enabled && guildId === '')}
        helperText={guildInvalid ? 'Must be a 17-20 digit ID' : 'Only members of this server can sign in'}
        onChange={(e) => onChange({ discord_login_guild_id: e.target.value.trim() })}
      />
      <TextField
        size="small"
        label="Required Role ID (optional)"
        value={roleId}
        error={roleInvalid}
        helperText={roleInvalid ? 'Must be a 17-20 digit ID' : 'Leave empty to allow every member'}
        onChange={(e) => onChange({ discord_login_required_role_id: e.target.value.trim() })}
      />
      <Stack spacing={0.5}>
        <Typography variant="caption" color="text.secondary">
          Permissions for new Discord accounts
        </Typography>
        <NativeSelect
          value={preset}
          onChange={(e) => onChange({ discord_login_default_preset: e.target.value })}
        >
          {PRESETS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </NativeSelect>
      </Stack>
      <FormControlLabel
        control={
          <Checkbox
            checked={disableOnLeave}
            onChange={(e) => onChange({ discord_login_disable_on_leave: e.target.checked })}
          />
        }
        label="Disable the account when someone leaves the server or loses the role"
      />
    </Stack>
  )
}

export default DiscordLogin
