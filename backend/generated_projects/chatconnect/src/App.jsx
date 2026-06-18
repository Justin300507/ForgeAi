import { useState } from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import Login from './pages/Login';
import Register from './pages/Register';
import Chat from './pages/Chat';
import GroupChat from './pages/GroupChat';
import Settings from './pages/Settings';

export default function App() {
  const [user, setUser] = useState(null);

  return (
    <Router>
      <Routes>
        <Route path="/login" element={<Login setUser={setUser} />} />
        <Route path="/register" element={<Register />} />
        <Route path="/chat" element={<Chat user={user} />} />
        <Route path="/group-chat" element={<GroupChat user={user} />} />
        <Route path="/settings" element={<Settings user={user} />} />
      </Routes>
    </Router>
  );
}