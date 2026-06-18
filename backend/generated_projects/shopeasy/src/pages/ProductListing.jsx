import React from 'react';
import ProductCard from '../components/ProductCard';

function ProductListing() {
  return (
    <div>
      <h1>All Products</h1>
      <div className="product-grid">
        {[...Array(6)].map((_, i) => (
          <ProductCard key={i} />
        ))}
      </div>
    </div>
  );
}

export default ProductListing;