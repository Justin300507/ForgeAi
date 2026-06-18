import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import Expenses from './pages/Expenses';
import Budgets from './pages/Budgets';
import Reports from './pages/Reports';
import Settings from './pages/Settings';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/expenses" element={<Expenses />} />
        <Route path="/budgets" element={<Budgets />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/settings" element={<Settings />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;