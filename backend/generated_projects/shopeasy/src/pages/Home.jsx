import React from 'react';
import ProductCard from '../components/ProductCard';

function Home() {
  return (
    <div>
      <h1>Welcome to our Store</h1>
      <div className="featured-products">
        <ProductCard />
      </div>
    </div>
  );
}

export default Home;