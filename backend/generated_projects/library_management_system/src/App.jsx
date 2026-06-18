import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { Home, BookSearch, MemberDashboard, TransactionHistory, AdminPanel } from './pages';
import { Navbar } from './components';

export default function App() {
  return (
    <Router>
      <Navbar />
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/books" element={<BookSearch />} />
        <Route path="/members" element={<MemberDashboard />} />
        <Route path="/transactions" element={<TransactionHistory />} />
        <Route path="/admin" element={<AdminPanel />} />
      </Routes>
    </Router>
  );
}