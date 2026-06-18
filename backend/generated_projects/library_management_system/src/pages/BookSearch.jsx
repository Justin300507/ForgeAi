import { useState, useEffect } from 'react';
import { BookCard, SearchBar } from '../components';

export default function BookSearch() {
  const [books, setBooks] = useState([]);
  
  useEffect(() => {
    // Fetch books from API
  }, []);
  
  return (
    <div>
      <SearchBar />
      {books.map(book => (
        <BookCard key={book.id} book={book} />
      ))}
    </div>
  );
}