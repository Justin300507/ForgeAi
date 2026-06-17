import React, { useState, useEffect } from 'react';
import axios from 'axios';

export default function Home() {
  const [books, setBooks] = useState([]);

  useEffect(() => {
    axios.get('/api/books')
      .then(res => setBooks(res.data))
      .catch(err => console.error(err));
  }, []);

  return (
    <div>
      <h1>Book Inventory</h1>
      {books.map(book => (
        <div key={book.id}>{book.title} by {book.author}</div>
      ))}
    </div>
  );
}