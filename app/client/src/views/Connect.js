import React from 'react'
import { useSearchParams, useNavigate, useLocation } from 'react-router-dom'
import { Box, Button, CircularProgress, Paper, Stack, Typography } from '@mui/material'
import LinkIcon from '@mui/icons-material/Link'
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline'
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline'
import BlockIcon from '@mui/icons-material/Block'
import { AuthService, UserService } from '../services'
import { DisableDragDrop } from '../components/utils/GlobalDragDropOverlay'

// An app such as SteamReplay opens this page with the code it shows the user.
// Allowing gives that app an upload token of this account; it collects the token
// itself, so nothing has to be copied.
const Connect = () => {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const location = useLocation()
  const code = params.get('code') || ''

  // loading | ask | approved | denied | invalid | forbidden | error
  const [state, setState] = React.useState('loading')
  const [request, setRequest] = React.useState(null)
  const [username, setUsername] = React.useState('')
  const [message, setMessage] = React.useState(null)
  const [busy, setBusy] = React.useState(false)

  React.useEffect(() => {
    let cancelled = false
    const load = async () => {
      if (!code) {
        setState('invalid')
        return
      }
      const auth = (await AuthService.isLoggedIn()).data
      if (cancelled) return
      if (!auth?.authenticated) {
        const next = location.pathname + location.search
        navigate(`/login?next=${encodeURIComponent(next)}`, { replace: true })
        return
      }
      setUsername(auth.display_name || auth.username)
      if (!auth.admin && !(auth.permissions || []).includes('upload')) {
        setState('forbidden')
        return
      }
      try {
        const res = await UserService.getConnectRequest(code)
        if (cancelled) return
        setRequest(res.data)
        setState(res.data.status === 'pending' ? 'ask' : res.data.status === 'denied' ? 'denied' : 'approved')
      } catch (err) {
        if (cancelled) return
        setState(err.response?.status === 404 ? 'invalid' : 'error')
        setMessage(err.response?.data?.error || null)
      }
    }
    load().catch(() => !cancelled && setState('error'))
    return () => {
      cancelled = true
    }
  }, [code, location.pathname, location.search, navigate])

  const decide = async (allow) => {
    setBusy(true)
    setMessage(null)
    try {
      if (allow) await UserService.approveConnectRequest(code)
      else await UserService.denyConnectRequest(code)
      setState(allow ? 'approved' : 'denied')
    } catch (err) {
      const status = err.response?.status
      const data = err.response?.data
      if (status === 404) setState('invalid')
      else if (status === 409 && data?.status) setState(data.status === 'denied' ? 'denied' : 'approved')
      else setMessage(data?.error || 'Something went wrong. Try again.')
    }
    setBusy(false)
  }

  const appName = request?.client_name || 'The app'

  return (
    <DisableDragDrop>
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: '100%',
          px: 2,
          py: 6,
          background: `
            radial-gradient(ellipse 80% 50% at 50% -10%, rgba(38, 132, 255, 0.12) 0%, transparent 70%),
            #001E3C
          `,
        }}
      >
        <Paper
          variant="outlined"
          sx={{ width: '100%', maxWidth: 440, p: 3.5, borderColor: '#1E4976', bgcolor: '#0A1929' }}
        >
          {state === 'loading' && (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
              <CircularProgress />
            </Box>
          )}

          {state === 'ask' && (
            <Stack spacing={2.5}>
              <Stack spacing={1} alignItems="center" sx={{ textAlign: 'center' }}>
                <LinkIcon sx={{ fontSize: 34, color: '#66B2FF' }} />
                <Typography sx={{ fontSize: 20, fontWeight: 700 }}>Connect an app</Typography>
                <Typography sx={{ fontSize: 14, color: '#B2BAC2' }}>
                  Allow{' '}
                  <Box component="span" sx={{ color: '#fff', fontWeight: 600 }}>
                    {appName}
                  </Box>{' '}
                  to upload clips as{' '}
                  <Box component="span" sx={{ fontFamily: 'monospace', color: '#66B2FF' }}>
                    @{username}
                  </Box>
                  ?
                </Typography>
              </Stack>

              <Box
                sx={{
                  textAlign: 'center',
                  fontFamily: 'monospace',
                  fontSize: 26,
                  letterSpacing: 4,
                  py: 1.5,
                  borderRadius: 1,
                  bgcolor: 'rgba(255,255,255,0.04)',
                  border: '1px solid #1E4976',
                }}
              >
                {request?.user_code}
              </Box>
              <Typography sx={{ fontSize: 13, color: 'rgba(255,255,255,0.55)', textAlign: 'center' }}>
                Only allow this if you just clicked Connect in the app and it shows the same code. The app gets
                an upload token, which you can delete any time under Settings → Security → Upload tokens.
              </Typography>

              {message && <Typography sx={{ fontSize: 13, color: '#FF6B6B' }}>{message}</Typography>}

              <Stack direction="row" spacing={1.5}>
                <Button variant="outlined" size="large" fullWidth disabled={busy} onClick={() => decide(false)}>
                  Deny
                </Button>
                <Button variant="contained" size="large" fullWidth disabled={busy} onClick={() => decide(true)}>
                  Allow
                </Button>
              </Stack>
            </Stack>
          )}

          {state === 'approved' && (
            <Stack spacing={2} alignItems="center" sx={{ textAlign: 'center', py: 2 }}>
              <CheckCircleOutlineIcon sx={{ fontSize: 46, color: '#1DB45A' }} />
              <Typography sx={{ fontSize: 19, fontWeight: 700 }}>Connected</Typography>
              <Typography sx={{ fontSize: 13.5, color: '#B2BAC2' }}>
                {appName} can upload now. You can close this tab.
              </Typography>
            </Stack>
          )}

          {state === 'denied' && (
            <Stack spacing={2} alignItems="center" sx={{ textAlign: 'center', py: 2 }}>
              <BlockIcon sx={{ fontSize: 46, color: '#B2BAC2' }} />
              <Typography sx={{ fontSize: 19, fontWeight: 700 }}>Not connected</Typography>
              <Typography sx={{ fontSize: 13.5, color: '#B2BAC2' }}>
                {appName} did not get access. You can close this tab.
              </Typography>
            </Stack>
          )}

          {(state === 'invalid' || state === 'forbidden' || state === 'error') && (
            <Stack spacing={2} alignItems="center" sx={{ textAlign: 'center', py: 2 }}>
              <ErrorOutlineIcon sx={{ fontSize: 46, color: '#FF6B6B' }} />
              <Typography sx={{ fontSize: 19, fontWeight: 700 }}>
                {state === 'forbidden' ? 'Uploading is not allowed' : 'Cannot connect'}
              </Typography>
              <Typography sx={{ fontSize: 13.5, color: '#B2BAC2' }}>
                {state === 'invalid' && 'This code is unknown or has expired. Click Connect again in the app.'}
                {state === 'forbidden' &&
                  'Your account may not upload, so it cannot connect an upload app. Ask an administrator.'}
                {state === 'error' && (message || 'Something went wrong. Reload the page to try again.')}
              </Typography>
            </Stack>
          )}
        </Paper>
      </Box>
    </DisableDragDrop>
  )
}

export default Connect
