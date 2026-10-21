"""Stricter recipe matching (FoodAssistant-9856m).

One shared word used to count an ingredient as owned ("baking powder" found
the baking soda, "chicken" found the chicken stock). These pins cover the
same-food test that replaced it: shared head word, harmless variety words
only on the recipe side, and no food-changing word on the stock side.
"""
import pytest

from app.services.recipe_match import _food_key, _ingredient_keys, _owns


@pytest.mark.parametrize("ingredient,stock", [
    ("chicken", "Chicken Breast"),
    ("boneless skinless chicken thighs", "Chicken Thighs"),
    ("2 cups shredded cheddar cheese", "Cheddar"),
    ("cheddar", "Cheddar Cheese"),
    ("1 yellow onion, diced", "Onions"),
    ("3 cloves garlic, minced", "Garlic"),
    ("extra virgin olive oil", "Olive Oil"),
    ("1 cup butter, softened", "Butter"),
    ("unsalted butter", "Butter"),
    ("whole milk", "Milk"),
    ("2 large eggs", "Eggs"),
    ("eggs", "Large Brown Eggs"),
    ("baby spinach", "Spinach"),
    ("roma tomatoes", "Tomatoes"),
    ("1 (14 oz) can diced tomatoes, drained", "Diced Tomatoes"),
    ("juice of 1 lemon", "Lemons"),
    ("greek yogurt", "Chobani Vanilla Greek Yogurt"),
    ("butter or margarine", "Butter"),
    ("russet potatoes", "Potatoes"),
    ("light brown sugar", "Brown Sugar"),
    ("parmesan cheese", "Parmesan"),
    ("ground beef", "Ground Beef 80/20"),
    ("long grain white rice", "Rice"),
    ("fresh basil leaves", "Basil"),
    ("carrots, peeled and chopped", "Carrots"),
    ("1 lb pasta", "Pasta"),
    ("spaghetti", "Spaghetti"),
    ("heavy cream", "Heavy Whipping Cream"),
    ("salmon fillets", "Salmon"),
    ("bacon", "Thick Cut Bacon"),
    ("black beans, rinsed", "Black Beans"),
    ("frozen peas", "Peas"),
    ("cream cheese, softened", "Cream Cheese"),
    ("chicken broth", "Chicken Broth"),
    ("tahini", "Tahini Paste"),
])
def test_same_food_is_owned(ingredient, stock):
    assert _owns(_ingredient_keys(ingredient), _food_key(stock))


@pytest.mark.parametrize("ingredient,stock", [
    ("baking powder", "Baking Soda"),
    ("chicken", "Chicken Stock"),
    ("chicken stock", "Chicken Breast"),
    ("milk", "Coconut Milk"),
    ("coconut milk", "Milk"),
    ("butter", "Peanut Butter"),
    ("peanut butter", "Butter"),
    ("apple", "Apple Juice"),
    ("tomato", "Tomato Paste"),
    ("garlic", "Garlic Powder"),
    ("onion", "Green Onions"),
    ("sweet potatoes", "Potatoes"),
    ("sour cream", "Heavy Cream"),
    ("cream cheese", "Sour Cream"),
    ("cheese", "Cream Cheese"),
    ("rice vinegar", "Rice"),
    ("rice", "Rice Vinegar"),
    ("corn syrup", "Maple Syrup"),
    ("lime", "Lemons"),
    ("turkey", "Chicken"),
    ("chocolate milk", "Milk"),
    ("salt", "Garlic Salt"),
    ("beef broth", "Chicken Broth"),
    ("almond flour", "Flour"),
])
def test_different_food_is_not_owned(ingredient, stock):
    assert not _owns(_ingredient_keys(ingredient), _food_key(stock))
