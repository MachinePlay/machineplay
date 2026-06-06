import { BrowserRouter, Route, Routes } from 'react-router'
import Layout from './Layout'
import { AuthProvider } from './auth'
import Home from './pages/Home'
import GamePage from './pages/GamePage'
import Engines from './pages/Engines'
import EngineDetail from './pages/EngineDetail'
import UploadInfo from './pages/UploadInfo'
import CliToken from './pages/CliToken'
import NotFound from './pages/NotFound'
import { ParamStub, Stub } from './pages/Stub'

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Home />} />
            <Route path="game/:id" element={<GamePage />} />
            <Route path="engine" element={<Engines />} />
            <Route path="engine/upload" element={<UploadInfo />} />
            <Route path="engine/:id" element={<EngineDetail />} />
            <Route path="cli" element={<CliToken />} />
            <Route path="tournament" element={<Stub title="tournaments" />} />
            <Route
              path="tournament/new"
              element={<Stub title="new tournament" />}
            />
            <Route
              path="tournament/:id"
              element={<ParamStub title="tournament" paramName="id" />}
            />
            <Route
              path="u/:login"
              element={<ParamStub title="user profile" paramName="login" />}
            />
            <Route
              path="about"
              element={
                <Stub
                  title="about"
                  note="MachinePlay — engines play, you watch. More soon."
                />
              }
            />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
