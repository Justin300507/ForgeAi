import { useState } from 'react';
import CategorySelector from './CategorySelector';

function ExpenseForm({ onSubmit }) {
  const [amount, setAmount] = useState('');
  const [category, setCategory] = useState('');
  
  const handleSubmit = (e) => {
    e.preventDefault();
    onSubmit({ amount, category });
    setAmount('');
    setCategory('');
  };
  
  return (
    <form onSubmit={handleSubmit}>
      <input 
        type="number" 
        value={amount} 
        onChange={(e) => setAmount(e.target.value)} 
        placeholder="Amount" 
      />
      <CategorySelector value={category} onChange={setCategory} />
      <button type="submit">Add Expense</button>
    </form>
  );
}

export default ExpenseForm;