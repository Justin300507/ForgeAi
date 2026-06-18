import { Link } from 'react-router-dom';

export default function Navbar() {
  return (
    <nav>
      <Link to="/">Home</Link>
      <Link to="/books">Books</Link>
      <Link to="/members">Members</Link>
      <Link to="/transactions">Transactions</Link>
    </nav>
  );
}